"""Tests for Super Admin adding other admins, updating phone numbers and passwords, and PATCH /me."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from app.admin.schemas import AdminCreateUserRequest, UpdateUserStatusRequest
from app.admin.service import create_user_admin, update_user_status_admin
from app.auth.models import User
from app.auth.router import update_me_endpoint
from app.auth.schemas import UpdateProfileRequest
from app.auth.dependencies import TeamContext
from app.core.exceptions import ConflictError, NotFoundError


@pytest.mark.anyio
async def test_admin_create_user_endpoint_service():
    """Verify Super Admin can directly create a new admin or promote an existing user."""
    mock_db = AsyncMock()

    # Case 1: Create a brand new admin user
    mock_res_none = MagicMock()
    mock_res_none.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_res_none

    req_new = AdminCreateUserRequest(
        phone="09123334455",
        password="AdminSecretPassword123",
        display_name="ادمین جدید",
        is_superuser=True,
    )
    user = await create_user_admin(mock_db, req_new)
    assert user.phone_number == "+989123334455"
    assert user.is_superuser is True
    assert user.is_verified is True
    assert user.display_name == "ادمین جدید"
    mock_db.add.assert_called_once_with(user)

    mock_db.reset_mock()

    # Case 2: Promote an existing user to Super Admin
    existing = User(
        id=8,
        phone_number="+989127778899",
        password_hash="old_pw",
        is_superuser=False,
    )
    mock_res_exist = MagicMock()
    mock_res_exist.scalar_one_or_none.return_value = existing
    mock_db.execute.return_value = mock_res_exist

    req_promote = AdminCreateUserRequest(
        phone="09127778899",
        password="NewAdminPassword123",
        display_name="کاربر ارتقا یافته",
        is_superuser=True,
    )
    promoted = await create_user_admin(mock_db, req_promote)
    assert promoted.id == 8
    assert promoted.is_superuser is True
    assert promoted.display_name == "کاربر ارتقا یافته"


@pytest.mark.anyio
async def test_admin_update_user_credentials_and_phone():
    """Verify Super Admin can update any user's phone, password, and superuser status."""
    mock_db = AsyncMock()

    user = User(
        id=12,
        phone_number="+989121112233",
        password_hash="initial_hash",
        display_name="نام قدیمی",
        is_superuser=False,
    )

    # 1. Update status, phone, and password
    mock_user_res = MagicMock()
    mock_user_res.scalar_one_or_none.return_value = user

    mock_dup_none = MagicMock()
    mock_dup_none.scalar_one_or_none.return_value = None

    def exec_side_effect(stmt, *args, **kwargs):
        stmt_str = str(stmt)
        if "id !=" in stmt_str:
            return mock_dup_none
        return mock_user_res

    mock_db.execute.side_effect = exec_side_effect

    update_req = UpdateUserStatusRequest(
        is_superuser=True,
        phone_number="09129990011",
        password="ChangedPassword456",
        display_name="نام جدید مدیر",
    )
    updated = await update_user_status_admin(mock_db, user_id=12, req=update_req)

    assert updated.is_superuser is True
    assert updated.phone_number == "+989129990011"
    assert updated.display_name == "نام جدید مدیر"

    # 2. Duplicate phone check rejects
    another_user = User(id=99, phone_number="+989129990011")
    mock_dup_exist = MagicMock()
    mock_dup_exist.scalar_one_or_none.return_value = another_user

    def exec_dup_side_effect(stmt, *args, **kwargs):
        stmt_str = str(stmt)
        if "id !=" in stmt_str:
            return mock_dup_exist
        return mock_user_res

    mock_db.execute.side_effect = exec_dup_side_effect

    with pytest.raises(ConflictError, match="این شماره موبایل توسط کاربر دیگری استفاده شده است"):
        await update_user_status_admin(mock_db, user_id=12, req=UpdateUserStatusRequest(phone_number="09129990011"))


@pytest.mark.anyio
async def test_update_me_endpoint_profile_and_phone():
    """Verify authenticated user can update display_name, password, and phone number via PATCH /me."""
    mock_db = AsyncMock()

    current_user = User(
        id=15,
        phone_number="+989120000000",
        display_name="مدیر اولیه",
        is_superuser=True,
        is_verified=True,
    )
    team = TeamContext(
        owner=current_user,
        current_user=current_user,
        role=None,
        actions={"*"},
        scope=[],
    )

    # 1. Successful self-update of phone number and password
    mock_dup_none = MagicMock()
    mock_dup_none.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_dup_none

    payload = UpdateProfileRequest(
        phone_number="09128887766",
        password="MyNewPersonalPassword999",
        display_name="شماره اختصاصی مدیر کل",
    )
    res = await update_me_endpoint(payload=payload, team=team, db=mock_db)

    assert res.phone_number == "+989128887766"
    assert res.display_name == "شماره اختصاصی مدیر کل"
    assert res.is_superuser is True

    # 2. Duplicate phone number check rejects
    other_user = User(id=30, phone_number="+989128887766")
    mock_dup_exist = MagicMock()
    mock_dup_exist.scalar_one_or_none.return_value = other_user
    mock_db.execute.return_value = mock_dup_exist

    with pytest.raises(ConflictError, match="این شماره موبایل توسط کاربر دیگری استفاده شده است"):
        await update_me_endpoint(
            payload=UpdateProfileRequest(phone_number="09128887766"),
            team=team,
            db=mock_db,
        )
