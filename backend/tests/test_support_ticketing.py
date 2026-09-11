"""Comprehensive tests for Customer Support, Threaded Discussions, and Staff Internal Notes."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.auth.dependencies import get_current_user, require_superuser
from app.auth.models import User
from app.core.database import get_db
from app.core.exceptions import AuthorizationError, ConflictError, InvalidInputError, NotFoundError
from app.main import app
from app.support.models import Ticket, TicketMessage
from app.support.schemas import (
    CreateTicketRequest,
    ReplyTicketRequest,
    UpdateTicketStatusRequest,
)
from app.support.service import (
    create_ticket,
    get_ticket_detail,
    list_all_tickets_admin,
    list_user_tickets,
    reply_to_ticket,
    update_ticket_status,
)


def test_create_ticket_and_safety_checks():
    """Verify ticket submission succeeds with clean inputs and blocks prohibited phrases."""
    async def _run():
        db = AsyncMock()
        user = User(id=5, phone_number="09125555555", password_hash="hash", is_superuser=False)

        # 1. Successful ticket creation
        valid_req = CreateTicketRequest(
            subject="مشکل در اتصال کانال تلگرام",
            category="technical",
            priority="high",
            initial_message="هنگام ارسال توکن بات، خطای اعتبارسنجی دریافت می‌کنم.",
            attachments=[{"filename": "error.png", "url": "https://s3.zeroio.io/error.png"}],
        )

        ticket = await create_ticket(db, user, valid_req)
        assert ticket.user_id == 5
        assert ticket.subject == "مشکل در اتصال کانال تلگرام"
        assert ticket.status == "open"
        assert ticket.priority == "high"

        # 2. Blocked by safety filter in subject
        unsafe_subject_req = CreateTicketRequest(
            subject="واریز وجه اجباری برای دریافت جایزه",
            initial_message="لطفا بررسی کنید",
        )
        with pytest.raises(InvalidInputError) as exc:
            await create_ticket(db, user, unsafe_subject_req)
        assert "غیرمجاز" in str(exc.value)

        # 3. Blocked by safety filter in initial message
        unsafe_msg_req = CreateTicketRequest(
            subject="درخواست واریز",
            initial_message="شماره کارت و رمز دوم پویا را برای شارژ بفرستید",
        )
        with pytest.raises(InvalidInputError) as exc:
            await create_ticket(db, user, unsafe_msg_req)
        assert "غیرمجاز" in str(exc.value)

    asyncio.run(_run())


def test_support_ticket_staff_notes_visibility():
    """Verify regular users CANNOT see staff internal notes, while superusers CAN."""
    async def _run():
        db = AsyncMock()
        regular_user = User(id=1, phone_number="09121111111", password_hash="hash", is_superuser=False)
        admin_user = User(id=99, phone_number="09129999999", password_hash="hash", is_superuser=True)

        # Ticket with 3 messages: 1 client, 1 internal note, 1 public staff reply
        ticket = Ticket(
            id=10,
            user_id=1,
            subject="پرسش در مورد فاکتور پرداخت",
            category="billing",
            priority="medium",
            status="waiting_user",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        msg1 = TicketMessage(
            id=101,
            ticket_id=10,
            sender_id=1,
            is_staff=False,
            is_internal_note=False,
            content="فاکتور دوره گذشته برای من صادر نشده است.",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )
        msg2_internal = TicketMessage(
            id=102,
            ticket_id=10,
            sender_id=99,
            is_staff=True,
            is_internal_note=True,
            content="یادداشت محرمانه: لاگ درگاه پرداخت نشان می‌دهد تراکنش معلق مانده است.",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )
        msg3_public = TicketMessage(
            id=103,
            ticket_id=10,
            sender_id=99,
            is_staff=True,
            is_internal_note=False,
            content="سلام، فاکتور شما بررسی شد و تا دقایقی دیگر به ایمیلتان ارسال خواهد شد.",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )

        ticket.messages = [msg1, msg2_internal, msg3_public]
        db.execute.return_value.scalar_one_or_none.return_value = ticket

        # Regular user view -> internal note MUST BE FILTERED OUT
        user_view = await get_ticket_detail(db, ticket_id=10, user=regular_user)
        assert len(user_view.messages) == 2
        assert all(not m.is_internal_note for m in user_view.messages)
        assert "یادداشت محرمانه" not in [m.content for m in user_view.messages]

        # Admin view -> internal note MUST BE VISIBLE
        admin_view = await get_ticket_detail(db, ticket_id=10, user=admin_user)
        assert len(admin_view.messages) == 3
        assert any(m.is_internal_note for m in admin_view.messages)

    asyncio.run(_run())


def test_ticket_reply_and_lifecycle_transitions():
    """Verify ticket replies update status, enforce closed ticket restrictions, and RBAC."""
    async def _run():
        db = AsyncMock()
        client_user = User(id=2, phone_number="09122222222", password_hash="hash", is_superuser=False)
        other_user = User(id=3, phone_number="09123333333", password_hash="hash", is_superuser=False)
        admin_user = User(id=99, phone_number="09129999999", password_hash="hash", is_superuser=True)

        ticket = Ticket(
            id=20,
            user_id=2,
            subject="خطا در زمانبندی",
            category="general",
            priority="low",
            status="open",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.execute.return_value.scalar_one_or_none.return_value = ticket

        # 1. Other user cannot reply to ticket -> AuthorizationError
        with pytest.raises(AuthorizationError):
            await reply_to_ticket(db, ticket_id=20, user=other_user, req=ReplyTicketRequest(content="پاسخ غیرمجاز"))

        # 2. Client reply updates status to 'in_progress'
        await reply_to_ticket(db, ticket_id=20, user=client_user, req=ReplyTicketRequest(content="توضیحات تکمیلی"))
        assert ticket.status == "in_progress"

        # 3. Superuser public reply updates status to 'waiting_user'
        await reply_to_ticket(
            db,
            ticket_id=20,
            user=admin_user,
            req=ReplyTicketRequest(content="پاسخ کارشناس فنی", is_internal_note=False),
        )
        assert ticket.status == "waiting_user"

        # 4. Close ticket
        await update_ticket_status(db, ticket_id=20, user=client_user, new_status="closed")
        assert ticket.status == "closed"

        # 5. Replying to a closed ticket raises ConflictError
        with pytest.raises(ConflictError):
            await reply_to_ticket(db, ticket_id=20, user=client_user, req=ReplyTicketRequest(content="سلام مجدد"))

    asyncio.run(_run())


def test_support_router_http_endpoints():
    """Verify FastAPI integration for support endpoints (users & superusers)."""
    async def _run():
        user = User(id=1, phone_number="09121111111", password_hash="hash", is_superuser=False)
        admin = User(id=99, phone_number="09129999999", password_hash="hash", is_superuser=True)

        mock_db = AsyncMock()

        ticket = Ticket(
            id=1,
            user_id=1,
            subject="پرسش فنی",
            category="technical",
            priority="medium",
            status="open",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            messages=[],
        )

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                app.dependency_overrides[get_current_user] = lambda: user
                app.dependency_overrides[require_superuser] = lambda: admin
                app.dependency_overrides[get_db] = lambda: mock_db

                mock_db.execute.return_value.scalar_one_or_none.return_value = ticket
                mock_db.execute.return_value.scalars.return_value.all.return_value = [ticket]

                # 1. POST /api/v1/support/tickets
                create_payload = {
                    "subject": "مشکل در آپلود ویدیو",
                    "category": "technical",
                    "priority": "medium",
                    "initial_message": "حجم ویدیو ۲۰ مگابایت است اما خطای تایم‌اوت می‌دهد.",
                    "attachments": [],
                }
                resp_create = await client.post("/api/v1/support/tickets", json=create_payload)
                assert resp_create.status_code == 201
                assert resp_create.json()["subject"] == "پرسش فنی"

                # 2. GET /api/v1/support/tickets
                resp_list = await client.get("/api/v1/support/tickets")
                assert resp_list.status_code == 200
                assert len(resp_list.json()) == 1

                # 3. POST /api/v1/support/tickets/1/reply
                reply_payload = {"content": "پاسخ از سمت کاربر"}
                resp_reply = await client.post("/api/v1/support/tickets/1/reply", json=reply_payload)
                assert resp_reply.status_code == 200

                # 4. POST /api/v1/support/tickets/1/close
                resp_close = await client.post("/api/v1/support/tickets/1/close")
                assert resp_close.status_code == 200

                # 5. GET /api/v1/support/admin/tickets (as admin)
                resp_admin_list = await client.get("/api/v1/support/admin/tickets")
                assert resp_admin_list.status_code == 200

                # 6. PATCH /api/v1/support/admin/tickets/1/status (as admin)
                resp_status = await client.patch(
                    "/api/v1/support/admin/tickets/1/status",
                    json={"status": "in_progress"},
                )
                assert resp_status.status_code == 200

        finally:
            app.dependency_overrides.clear()

    asyncio.run(_run())
