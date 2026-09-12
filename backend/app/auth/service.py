"""Auth business logic: registration, login, refresh, password reset."""

import inspect
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import OtpCode, RefreshToken, User, MemberInvite, Role
from app.auth.schemas import TokenResponse, ActivateRequest, InviteRequest , MemberUpdate , RoleUpdate
from app.auth.permissions import ACTION_CATALOG, owner_only_actions, validate_actions
from app.auth.security import (
    create_access_token,
    generate_otp_code,
    hash_otp_code,
    hash_password,
    hash_token,
    normalize_phone_number,
    verify_password,
)
from app.auth.sms import build_webotp_message, get_sms_sender
from app.core.audit import log_audit
from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
)


MAX_OTP_ATTEMPTS = 5
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 15


def _normalize(raw: str) -> str:
    """Normalize raw phone input or raise InvalidInputError."""
    try:
        return normalize_phone_number(raw)
    except Exception as exc:
        raise InvalidInputError("شماره موبایل معتبر نیست.") from exc


def _now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def _issue_tokens(db: AsyncSession, user: User, device_info: str | None) -> TokenResponse:
    """Create an access token plus a fresh refresh-token family member."""
    settings = get_settings()
    raw_refresh = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            family=str(uuid4()),
            device_info=device_info,
            expires_at=_now() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return TokenResponse(access_token=create_access_token(user.id), refresh_token=raw_refresh)


async def _consume_otp(db: AsyncSession, phone: str, purpose: str, code: str) -> OtpCode:
    """Validate an OTP code, enforcing expiry, attempt cap, and single use."""
    result = await db.execute(
        select(OtpCode)
        .where(
            OtpCode.phone_number == phone,
            OtpCode.purpose == purpose,
            OtpCode.used_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    otp = result.scalar_one_or_none()
    if otp is None:
        raise NotFoundError("کدی برای این شماره یافت نشد؛ دوباره درخواست دهید.")
    if otp.expires_at <= _now():
        raise AuthenticationError("کد منقضی شده است.")
    if otp.attempts >= MAX_OTP_ATTEMPTS:
        raise AuthenticationError("تعداد تلاش‌ها تمام شد؛ کد جدید درخواست دهید.")
    if otp.code_hash != hash_otp_code(code):
        otp.attempts += 1
        await db.commit()  # Critical: persist attempt count to prevent rollback on AuthenticationError
        raise AuthenticationError("کد نادرست است.")
    otp.used_at = _now()
    return otp


async def _invalidate_previous_otps(db: AsyncSession, phone: str, purpose: str) -> None:
    """Expire all unused OTPs for the phone+purpose before issuing a new one."""
    await db.execute(
        update(OtpCode)
        .where(
            OtpCode.phone_number == phone,
            OtpCode.purpose == purpose,
            OtpCode.used_at.is_(None),
        )
        .values(expires_at=_now())
    )


async def request_register(
    db: AsyncSession,
    raw_phone: str,
    ip_address: str | None = None,
    honeypot: str | None = None,
) -> str:
    """Send a registration OTP; returns the code (dev-only exposure)."""
    if honeypot:
        return "000000"  # silent success for bots; nothing stored or sent
    phone = _normalize(raw_phone)

    already = await db.execute(
        select(exists().where(User.phone_number == phone, User.is_verified.is_(True)))
    )
    already_val = already.scalar_one()
    if inspect.isawaitable(already_val):
        already_val = await already_val
    if bool(already_val):
        raise ConflictError("این شماره قبلاً ثبت‌نام کرده است.")

    code = generate_otp_code()
    await _invalidate_previous_otps(db, phone, "register")
    res_add = db.add(
        OtpCode(
            phone_number=phone,
            purpose="register",
            code_hash=hash_otp_code(code),
            expires_at=_now() + timedelta(minutes=get_settings().otp_ttl_minutes),
            ip_address=ip_address,
        )
    )
    if inspect.isawaitable(res_add):
        await res_add
    message = build_webotp_message(code=code)
    get_sms_sender().send(phone, message, otp_code=code)
    return code


async def verify_register(
    db: AsyncSession,
    raw_phone: str,
    code: str,
    password: str,
    display_name: str | None,
    device_info: str | None,
    ip_address: str | None = None,
) -> TokenResponse:
    """Verify the OTP, create/activate the user, and issue tokens."""
    phone = _normalize(raw_phone)
    await _consume_otp(db, phone, "register", code)

    result = await db.execute(select(User).where(User.phone_number == phone))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            phone_number=phone,
            password_hash=hash_password(password),
            display_name=display_name,
            is_verified=True,
            owner_user_id=None,
            role_id=None,
        )
        db.add(user)
    else:
        user.password_hash = hash_password(password)
        user.is_verified = True
        user.owner_user_id = None
        user.role_id = None
        if display_name:
            user.display_name = display_name
    await db.flush()
    await log_audit(
        db, "user.registered", user_id=user.id, ip_address=ip_address, user_agent=device_info
    )
    return _issue_tokens(db, user, device_info)


async def login(
    db: AsyncSession,
    raw_phone: str,
    password: str,
    device_info: str | None,
    ip_address: str | None = None,
) -> TokenResponse:
    """Authenticate with phone+password, enforcing lockout policy."""
    phone = _normalize(raw_phone)
    generic = AuthenticationError("شماره یا رمز عبور اشتباه است.")

    result = await db.execute(select(User).where(User.phone_number == phone))
    user = result.scalar_one_or_none()
    if user is None or not user.is_verified:
        raise generic
    if not user.is_active:
        raise AuthenticationError("حساب کاربری غیرفعال است.")
    if user.locked_until is not None and user.locked_until > _now():
        raise AuthenticationError("حساب موقتاً قفل است؛ دقایقی بعد تلاش کنید.")

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = _now() + timedelta(minutes=LOGIN_LOCK_MINUTES)
            user.failed_login_attempts = 0
            get_sms_sender().send(
                user.phone_number,
                "حساب شما به دلیل تلاش‌های ناموفق مکرر موقتاً قفل شد.",
            )
            await log_audit(
                db, "account.locked", user_id=user.id,
                ip_address=ip_address, user_agent=device_info,
            )
        raise generic

    user.failed_login_attempts = 0
    user.locked_until = None
    return _issue_tokens(db, user, device_info)


async def refresh(
    db: AsyncSession, raw_refresh: str, device_info: str | None
) -> TokenResponse:
    """Rotate a refresh token; reuse of a revoked token kills the family."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh))
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise AuthenticationError("نشست معتبر نیست؛ دوباره وارد شوید.")

    now = _now()
    if token.revoked_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family == token.family)
            .values(revoked_at=now)
        )
        raise AuthenticationError("نشست معتبر نیست؛ دوباره وارد شوید.")
    if token.expires_at <= now:
        raise AuthenticationError("نشست منقضی شده است؛ دوباره وارد شوید.")

    token.revoked_at = now
    raw_new = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=token.user_id,
            token_hash=hash_token(raw_new),
            family=token.family,
            device_info=device_info,
            expires_at=now + timedelta(days=get_settings().refresh_token_ttl_days),
        )
    )
    return TokenResponse(access_token=create_access_token(token.user_id), refresh_token=raw_new)


async def logout(db: AsyncSession, raw_refresh: str) -> None:
    """Revoke the presented refresh token."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh))
    )
    token = result.scalar_one_or_none()
    if token is not None:
        token.revoked_at = _now()


async def request_reset(db: AsyncSession, raw_phone: str, ip_address: str | None = None) -> str | None:
    """Send a reset OTP if the account exists; never leaks existence."""
    try:
        phone = _normalize(raw_phone)
    except InvalidInputError:
        return None

    exists_result = await db.execute(
        select(exists().where(User.phone_number == phone, User.is_verified.is_(True)))
    )
    exists_val = exists_result.scalar_one()
    if inspect.isawaitable(exists_val):
        exists_val = await exists_val
    if not bool(exists_val):
        return None

    code = generate_otp_code()
    await _invalidate_previous_otps(db, phone, "reset")
    res_add = db.add(
        OtpCode(
            phone_number=phone,
            purpose="reset",
            code_hash=hash_otp_code(code),
            expires_at=_now() + timedelta(minutes=get_settings().otp_ttl_minutes),
            ip_address=ip_address,
        )
    )
    if inspect.isawaitable(res_add):
        await res_add
    message = build_webotp_message(code=code)
    get_sms_sender().send(phone, message, otp_code=code)
    return code


async def confirm_reset(
    db: AsyncSession, raw_phone: str, code: str, new_password: str, ip_address: str | None = None
) -> None:
    """Set a new password and revoke every session of the user."""
    phone = _normalize(raw_phone)
    await _consume_otp(db, phone, "reset", code)

    result = await db.execute(select(User).where(User.phone_number == phone))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("کاربر یافت نشد.")
    user.password_hash = hash_password(new_password)
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked_at=_now())
    )
    await log_audit(db, "password.changed", user_id=user.id, ip_address=ip_address)


async def _get_owner(db: AsyncSession, user: User) -> User:
    """Return the team owner (self if owner, else the owner_user)."""
    if user.owner_user_id is None:
        return user
    result = await db.execute(select(User).where(User.id == user.owner_user_id))
    return result.scalar_one()


async def invite_member(
    db: AsyncSession, owner: User, payload: InviteRequest, ip_address: str | None
) -> MemberInvite:
    """Create a pending invite and send an OTP with purpose=invite."""
    result = await db.execute(select(Role).where(Role.id == payload.role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise NotFoundError("نقش یافت نشد.")
    if role.team_owner_id != owner.id:
        raise NotFoundError("نقش یافت نشد.")

    phone = _normalize(payload.phone)

    # Check if user already exists

    res_existing = await db.execute(select(User).where(User.phone_number == phone))
    existing_user = res_existing.scalar_one_or_none()
    if inspect.isawaitable(existing_user):
        existing_user = await existing_user

    if existing_user is not None:
        if existing_user.id == owner.id:
            raise ConflictError("نمی‌توانید خودتان را به عنوان عضو دعوت کنید.")
        if existing_user.owner_user_id == owner.id:
            raise ConflictError("این کاربر در حال حاضر عضو تیم شما است.")
        if existing_user.owner_user_id is not None and existing_user.owner_user_id != owner.id:
            raise ConflictError("این کاربر در حال حاضر عضو تیم دیگری است.")

    pending = await db.execute(
        select(MemberInvite).where(
            MemberInvite.phone_number == phone,
            MemberInvite.owner_id == owner.id,
            MemberInvite.status == "pending",
            MemberInvite.expires_at > _now(),
        )
    )
    res_pending = pending.scalar_one_or_none()
    if inspect.isawaitable(res_pending):
        res_pending = await res_pending
    if res_pending is not None:
        raise ConflictError("برای این شماره دعوت فعال وجود دارد.")

    code = generate_otp_code()
    await _invalidate_previous_otps(db, phone, "invite")
    res_add = db.add(
        OtpCode(
            phone_number=phone,
            purpose="invite",
            code_hash=hash_otp_code(code),
            expires_at=_now() + timedelta(minutes=get_settings().otp_ttl_minutes),
            ip_address=ip_address,
        )
    )
    if inspect.isawaitable(res_add):
        await res_add
    invite = MemberInvite(
        owner_id=owner.id,
        phone_number=phone,
        role_id=role.id,
        status="pending",
        expires_at=_now() + timedelta(hours=48),
    )
    db.add(invite)
    invite_message = build_webotp_message(code=code, app_name=f"دعوت به تیم {owner.display_name or 'مزون‌فلو'}")
    get_sms_sender().send(phone, invite_message, otp_code=code)
    await log_audit(
        db, "member.invited", user_id=owner.id, resource_type="invite",
        ip_address=ip_address,
    )
    await db.flush() 
    invite.role = role
    return invite


async def activate_invite(
    db: AsyncSession,
    raw_phone: str,
    code: str,
    password: str,
    display_name: str | None,
    device_info: str | None,
    ip_address: str | None,
) -> TokenResponse:
    """Verify invite OTP, link existing user or create member, and issue tokens."""
    phone = _normalize(raw_phone)
    await _consume_otp(db, phone, "invite", code)

    result = await db.execute(
        select(MemberInvite).where(
            MemberInvite.phone_number == phone,
            MemberInvite.status == "pending",
            MemberInvite.expires_at > _now(),
        )
    )
    invite = result.scalar_one_or_none()
    if inspect.isawaitable(invite):
        invite = await invite
    if invite is None:
        raise NotFoundError("دعوت معتبر یافت نشد.")

    # Link existing user or create new user
    res_existing = await db.execute(select(User).where(User.phone_number == phone))
    existing_user = res_existing.scalar_one_or_none()
    if inspect.isawaitable(existing_user):
        existing_user = await existing_user

    if existing_user is not None:
        existing_user.owner_user_id = invite.owner_id
        existing_user.role_id = invite.role_id
        existing_user.is_verified = True
        if display_name:
            existing_user.display_name = display_name
        if password:
            existing_user.password_hash = hash_password(password)
        user = existing_user
    else:
        user = User(
            phone_number=phone,
            password_hash=hash_password(password),
            display_name=display_name,
            is_verified=True,
            owner_user_id=invite.owner_id,
            role_id=invite.role_id,
        )
        db.add(user)

    invite.status = "used"
    await db.flush()
    await log_audit(
        db, "member.activated", user_id=user.id,
        resource_type="invite", resource_id=str(invite.id),
        ip_address=ip_address, user_agent=device_info,
    )
    return _issue_tokens(db, user, device_info)


async def list_members(db: AsyncSession, owner: User) -> list[User]:
    """List all members of the team with roles eager-loaded."""
    result = await db.execute(
        select(User)
        .where(User.owner_user_id == owner.id)
        .options(selectinload(User.role))
        .order_by(User.id)
    )
    return list(result.scalars().all())


async def update_member(
    db: AsyncSession, owner: User, member_id: int, payload: MemberUpdate
) -> User:
    """Update a member's role or active status; deactivation kills sessions."""
    result = await db.execute(
        select(User).where(User.id == member_id, User.owner_user_id == owner.id)
        .options(selectinload(User.role))
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("عضو یافت نشد.")
    if payload.role_id is not None:
        role_result = await db.execute(
            select(Role).where(Role.id == payload.role_id, Role.team_owner_id == owner.id)
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            raise NotFoundError("نقش یافت نشد.")
        member.role_id = role.id
    if payload.is_active is not None:
        if not payload.is_active:
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == member.id)
                .values(revoked_at=_now())
            )
        member.is_active = payload.is_active
    return member


async def delete_member(db: AsyncSession, owner: User, member_id: int) -> None:
    """Unlink a member from the team and revoke all sessions."""
    result = await db.execute(
        select(User).where(User.id == member_id, User.owner_user_id == owner.id)
    )
    member = result.scalar_one_or_none()
    if inspect.isawaitable(member):
        member = await member
    if member is None:
        raise NotFoundError("عضو یافت نشد.")
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == member.id)
        .values(revoked_at=_now())
    )
    await log_audit(
        db, "member.deleted", user_id=owner.id,
        resource_type="member", resource_id=str(member_id),
    )
    # Safely detach member from team without destroying user account
    member.owner_user_id = None
    member.role_id = None
    

async def update_role(
    db: AsyncSession, owner: User, role_id: int, payload: RoleUpdate
) -> Role:
    """Update a team role (owner-only)."""
    result = await db.execute(
        select(Role).where(Role.id == role_id, Role.team_owner_id == owner.id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise NotFoundError("نقش یافت نشد.")
    if payload.name is not None:
        role.name = payload.name
    if payload.actions is not None:
        from app.auth.permissions import validate_actions, owner_only_actions
        try:
            validate_actions(payload.actions)
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        forbidden = set(payload.actions) & owner_only_actions()
        if forbidden:
            raise InvalidInputError(f"این اکشن‌ها قابل تفویض نیستند: {forbidden}")
        role.actions = payload.actions
    if payload.scope is not None:
        role.scope = payload.scope
    return role