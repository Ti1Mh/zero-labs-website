"""Authentication endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.dependencies import TeamContext, get_current_team, require , get_current_user
from app.auth.permissions import owner_only_actions, validate_actions
from app.auth.models import User, Role
from app.auth.schemas import (
    ActivateRequest,
    InviteOut,
    InviteRequest,
    LoginRequest,
    MessageResponse,
    OtpResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterVerifyRequest,
    ResetConfirmRequest,
    ResetRequest,
    RoleCreate,
    RoleUpdate,
    RoleOut,
    TokenResponse,
    UserOut,
    MemberOut,
    MemberUpdate,
    
)
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import enforce_otp_rate_limits


router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _device(request: Request) -> str | None:
    """Extract the client user-agent as device info."""
    return request.headers.get("user-agent")


def _ip(request: Request) -> str | None:
    """Extract the client IP address (supporting X-Forwarded-For reverse proxies)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/register/request", response_model=OtpResponse)
async def register_request(
    payload: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> OtpResponse:
    """Send a registration OTP to the given phone number with multi-tier rate limiting."""
    client_ip = _ip(request)
    redis_client = getattr(request.app.state, "redis", None)
    await enforce_otp_rate_limits(redis_client, payload.phone, client_ip)

    code = await service.request_register(
        db, payload.phone, ip_address=client_ip, honeypot=payload.website
    )
    return OtpResponse(
        message="کد تأیید ارسال شد.",
        dev_otp_code=code if settings.debug else None,
    )



@router.post("/register/verify", response_model=TokenResponse)
async def register_verify(
    payload: RegisterVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """Verify the OTP, create the account, and return tokens."""
    return await service.verify_register(
        db, payload.phone, payload.code, payload.password,
        payload.display_name, _device(request), ip_address=_ip(request),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """Authenticate with phone and password."""
    return await service.login(
        db, payload.phone, payload.password, _device(request), ip_address=_ip(request)
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """Rotate the refresh token and issue a new access token."""
    return await service.refresh(db, payload.refresh_token, _device(request))


@router.post("/logout", response_model=MessageResponse)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    """Revoke the presented refresh token."""
    await service.logout(db, payload.refresh_token)
    return MessageResponse(message="خروج انجام شد.")


@router.post("/password-reset/request", response_model=OtpResponse)
async def password_reset_request(
    payload: ResetRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> OtpResponse:
    """Send a reset OTP without leaking account existence, with multi-tier rate limiting."""
    client_ip = _ip(request)
    redis_client = getattr(request.app.state, "redis", None)
    await enforce_otp_rate_limits(redis_client, payload.phone, client_ip)

    code = await service.request_reset(db, payload.phone, ip_address=client_ip)
    return OtpResponse(
        message="اگر شماره ثبت شده باشد، کد ارسال شد.",
        dev_otp_code=code if (settings.debug and code) else None,
    )



@router.post("/password-reset/confirm", response_model=MessageResponse)
async def password_reset_confirm(
    payload: ResetConfirmRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Set a new password and invalidate all sessions."""
    await service.confirm_reset(
        db, payload.phone, payload.code, payload.new_password, ip_address=_ip(request)
    )
    return MessageResponse(message="رمز عبور تغییر کرد؛ دوباره وارد شوید.")


@router.get("/me", response_model=UserOut)
async def me(team: TeamContext = Depends(get_current_team)) -> UserOut:
    """Return the authenticated user's profile and permissions."""
    return UserOut(
        id=team.current_user.id,
        phone_number=team.current_user.phone_number,
        display_name=team.current_user.display_name,
        is_verified=team.current_user.is_verified,
        is_owner=(team.role is None),
        is_superuser=team.current_user.is_superuser,
        actions=list(team.actions),
        scope=team.scope,
    )


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[RoleOut]:
    """List all roles owned by the current user's team."""
    owner = await service._get_owner(db, user)
    result = await db.execute(select(Role).where(Role.team_owner_id == owner.id))
    return [RoleOut.model_validate(r) for r in result.scalars().all()]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    payload: RoleCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    """Create a new role (owner-only action)."""
    if user.owner_user_id is not None:
        raise AuthenticationError("فقط مالک می‌تواند نقش بسازد.")
    try:
        validate_actions(payload.actions)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    forbidden = set(payload.actions) & owner_only_actions()
    if forbidden:
        raise InvalidInputError(f"این اکشن‌ها قابل تفویض نیستند: {forbidden}")
    role = Role(
        team_owner_id=user.id,
        name=payload.name,
        actions=payload.actions,
        scope=payload.scope,
    )
    db.add(role)
    await db.flush()
    return RoleOut.model_validate(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role_endpoint(
    role_id: int,
    payload: RoleUpdate,
    team: TeamContext = Depends(require("members:manage")),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    """Update a team role (owner-only)."""
    if team.role is not None:
        raise AuthorizationError("فقط مالک می‌تواند نقش‌ها را ویرایش کند.")
    role = await service.update_role(db, team.owner, role_id, payload)
    return RoleOut.model_validate(role)


@router.post("/members/invite", response_model=InviteOut, status_code=201)
async def invite_member(
    payload: InviteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    """Invite a new team member (owner-only action)."""
    if user.owner_user_id is not None:
        raise AuthenticationError("فقط مالک می‌تواند دعوت کند.")
    owner = await service._get_owner(db, user)
    invite = await service.invite_member(db, owner, payload, ip_address=_ip(request))
    await db.refresh(invite)
    return InviteOut.model_validate(invite)


@router.post("/members/activate", response_model=TokenResponse)
async def activate_member(
    payload: ActivateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Activate an invite with OTP and set a password."""
    return await service.activate_invite(
        db, payload.phone, payload.code, payload.password,
        payload.display_name, _device(request), ip_address=_ip(request),
    )
    
    
@router.get("/members", response_model=list[MemberOut])
async def list_members_endpoint(
    team: TeamContext = Depends(require("members:manage")),
    db: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    """List all team members (owner-only)."""
    members = await service.list_members(db, team.owner)
    return [MemberOut.model_validate(m) for m in members]


@router.patch("/members/{member_id}", response_model=MemberOut)
async def update_member_endpoint(
    member_id: int,
    payload: MemberUpdate,
    team: TeamContext = Depends(require("members:manage")),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    """Update a member's role or active status (owner-only)."""
    member = await service.update_member(db, team.owner, member_id, payload)
    return MemberOut.model_validate(member)


@router.delete("/members/{member_id}", status_code=204)
async def delete_member_endpoint(
    member_id: int,
    team: TeamContext = Depends(require("members:manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a member and revoke all sessions (owner-only)."""
    await service.delete_member(db, team.owner, member_id)