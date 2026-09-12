"""Tests for Super Admin seeding, UserOut schema, and team employee invites with existing registered accounts."""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import pytest

from app.auth.models import MemberInvite, Role, User
from app.auth.schemas import InviteRequest, UserOut
from app.auth.security import hash_password
from app.auth.service import activate_invite, delete_member, invite_member
from app.core.exceptions import ConflictError, NotFoundError
from create_admin import create_or_promote_admin, normalize_phone


def test_normalize_phone():
    """Verify phone normalization logic for admin creation."""
    assert normalize_phone("09123456789") == "+989123456789"
    assert normalize_phone("00989123456789") == "+989123456789"
    assert normalize_phone("+989123456789") == "+989123456789"
    assert normalize_phone("9123456789") == "+989123456789"


def test_user_out_is_superuser_field():
    """Verify UserOut includes is_superuser with default False."""
    regular_out = UserOut(
        id=1,
        phone_number="+989121111111",
        display_name="User",
        is_verified=True,
        is_owner=True,
        actions=["content:create"],
        scope=[],
    )
    assert regular_out.is_superuser is False

    admin_out = UserOut(
        id=2,
        phone_number="+989120000000",
        display_name="Admin",
        is_verified=True,
        is_owner=True,
        is_superuser=True,
        actions=["*"],
        scope=[],
    )
    assert admin_out.is_superuser is True


@pytest.mark.anyio
async def test_create_or_promote_admin_flow():
    """Verify create_or_promote_admin handles both creation and promotion."""
    mock_db = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__.return_value = mock_db
    mock_ctx.__aexit__.return_value = None

    # Case 1: User does not exist -> creates user
    mock_result_none = MagicMock()
    mock_result_none.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result_none

    with patch("create_admin.AsyncSessionLocal", return_value=mock_ctx):
        await create_or_promote_admin("09120000000", "AdminPass123", "مدیر سیستم")
        mock_db.add.assert_called_once()
        created_user = mock_db.add.call_args[0][0]
        assert isinstance(created_user, User)
        assert created_user.phone_number == "+989120000000"
        assert created_user.is_superuser is True
        assert created_user.is_verified is True
        mock_db.commit.assert_called_once()

    mock_db.reset_mock()

    # Case 2: User already exists -> promotes user
    existing_user = User(
        id=10,
        phone_number="+989129999999",
        password_hash="old_hash",
        is_superuser=False,
        is_verified=False,
    )
    mock_result_existing = MagicMock()
    mock_result_existing.scalar_one_or_none.return_value = existing_user
    mock_db.execute.return_value = mock_result_existing

    with patch("create_admin.AsyncSessionLocal", return_value=mock_ctx):
        await create_or_promote_admin("09129999999", "NewPass456", "مدیر ارتقا یافته")
        assert existing_user.is_superuser is True
        assert existing_user.is_verified is True
        assert existing_user.display_name == "مدیر ارتقا یافته"
        mock_db.commit.assert_called_once()
        assert existing_user.is_verified is True
        assert existing_user.display_name == "مدیر ارتقا یافته"
        mock_db.commit.assert_called_once()


@pytest.mark.anyio
async def test_invite_member_with_existing_registered_account():
    """Verify that an owner CAN invite a user who already has a registered standalone account."""
    mock_db = AsyncMock()

    owner = User(id=1, phone_number="+989121111111", display_name="صاحب مزون")
    role = Role(id=5, team_owner_id=1, name="نویسنده")

    # Mock Role query
    mock_role_res = MagicMock()
    mock_role_res.scalar_one_or_none.return_value = role

    # Mock existing user query (user is registered standalone: owner_user_id is None)
    employee_user = User(id=2, phone_number="+989122222222", owner_user_id=None, is_verified=True)
    mock_user_res = MagicMock()
    mock_user_res.scalar_one_or_none.return_value = employee_user

    # Mock pending invite query (no pending invite)
    mock_pending_res = MagicMock()
    mock_pending_res.scalar_one_or_none.return_value = None

    def execute_side_effect(statement, *args, **kwargs):
        stmt_str = str(statement)
        if "roles" in stmt_str:
            return mock_role_res
        if "users" in stmt_str:
            return mock_user_res
        if "member_invites" in stmt_str:
            return mock_pending_res
        return MagicMock()

    mock_db.execute.side_effect = execute_side_effect

    with patch("app.auth.service._enforce_otp_rate_limit", new_callable=AsyncMock), \
         patch("app.auth.service._enforce_ip_rate_limit", new_callable=AsyncMock), \
         patch("app.auth.service.get_sms_sender") as mock_sms_factory:

        mock_sms = MagicMock()
        mock_sms_factory.return_value = mock_sms

        payload = InviteRequest(phone="09122222222", role_id=5)
        invite = await invite_member(mock_db, owner, payload, ip_address="127.0.0.1")

        assert invite.phone_number == "+989122222222"
        assert invite.owner_id == 1
        assert invite.role_id == 5
        mock_sms.send.assert_called_once()


@pytest.mark.anyio
async def test_invite_member_prevent_self_or_duplicate_team():
    """Verify owner cannot invite self or an existing team member."""
    mock_db = AsyncMock()
    owner = User(id=1, phone_number="+989121111111")
    role = Role(id=5, team_owner_id=1, name="نویسنده")

    mock_role_res = MagicMock()
    mock_role_res.scalar_one_or_none.return_value = role

    # 1. Invite self
    mock_self_res = MagicMock()
    mock_self_res.scalar_one_or_none.return_value = owner

    def exec_self(stmt, *args, **kwargs):
        if "roles" in str(stmt):
            return mock_role_res
        return mock_self_res

    mock_db.execute.side_effect = exec_self

    with patch("app.auth.service._enforce_otp_rate_limit", new_callable=AsyncMock), \
         patch("app.auth.service._enforce_ip_rate_limit", new_callable=AsyncMock):
        with pytest.raises(ConflictError, match="نمی‌توانید خودتان را به عنوان عضو دعوت کنید"):
            await invite_member(mock_db, owner, InviteRequest(phone="09121111111", role_id=5), None)

    # 2. User already in team
    existing_member = User(id=3, phone_number="+989123333333", owner_user_id=1)
    mock_member_res = MagicMock()
    mock_member_res.scalar_one_or_none.return_value = existing_member

    def exec_member(stmt, *args, **kwargs):
        if "roles" in str(stmt):
            return mock_role_res
        return mock_member_res

    mock_db.execute.side_effect = exec_member

    with patch("app.auth.service._enforce_otp_rate_limit", new_callable=AsyncMock), \
         patch("app.auth.service._enforce_ip_rate_limit", new_callable=AsyncMock):
        with pytest.raises(ConflictError, match="این کاربر در حال حاضر عضو تیم شما است"):
            await invite_member(mock_db, owner, InviteRequest(phone="09123333333", role_id=5), None)


@pytest.mark.anyio
async def test_activate_invite_links_existing_user_without_duplicate():
    """Verify activate_invite updates existing user rather than attempting duplicate db.add."""
    mock_db = AsyncMock()

    invite = MemberInvite(
        id=7,
        phone_number="+989124444444",
        owner_id=1,
        role_id=3,
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    existing_user = User(
        id=4,
        phone_number="+989124444444",
        password_hash="old_pw",
        owner_user_id=None,
        role_id=None,
    )

    mock_invite_res = MagicMock()
    mock_invite_res.scalar_one_or_none.return_value = invite

    mock_user_res = MagicMock()
    mock_user_res.scalar_one_or_none.return_value = existing_user

    def exec_activate(stmt, *args, **kwargs):
        if "member_invites" in str(stmt):
            return mock_invite_res
        if "users" in str(stmt):
            return mock_user_res
        return MagicMock()

    mock_db.execute.side_effect = exec_activate

    with patch("app.auth.service._consume_otp", new_callable=AsyncMock), \
         patch("app.auth.service._issue_tokens") as mock_tokens:

        mock_tokens.return_value = MagicMock(access_token="acc", refresh_token="ref")

        await activate_invite(
            mock_db,
            raw_phone="09124444444",
            code="123456",
            password="NewPassword123",
            display_name="علی کارمند",
            device_info="iPhone",
            ip_address="127.0.0.1",
        )

        # Existing user must be updated with the team owner and role
        assert existing_user.owner_user_id == 1
        assert existing_user.role_id == 3
        assert existing_user.display_name == "علی کارمند"
        # Must NOT call db.add for a User (since user already exists!)
        assert not any(isinstance(call[0][0], User) for call in mock_db.add.call_args_list)


@pytest.mark.anyio
async def test_delete_member_unlinks_without_deleting_user_account():
    """Verify delete_member safely unlinks the user from the team without deleting the account."""
    mock_db = AsyncMock()
    owner = User(id=1, phone_number="+989121111111")
    member = User(id=5, phone_number="+989125555555", owner_user_id=1, role_id=2)

    mock_member_res = MagicMock()
    mock_member_res.scalar_one_or_none.return_value = member
    mock_db.execute.return_value = mock_member_res

    await delete_member(mock_db, owner, member_id=5)

    assert member.owner_user_id is None
    assert member.role_id is None
    # Must NOT call db.delete(member)
    mock_db.delete.assert_not_called()
