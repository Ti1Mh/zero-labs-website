"""Service layer for Support Desk ticket workflows, message threads, and staff notes."""

import inspect
import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.safety import check_content_safety
from app.auth.models import User
from app.core.exceptions import AuthorizationError, ConflictError, InvalidInputError, NotFoundError
from app.support.models import Ticket, TicketMessage
from app.support.schemas import (
    CreateTicketRequest,
    ReplyTicketRequest,
    TicketDetailResponse,
    TicketListItemResponse,
    TicketMessageResponse,
)

logger = logging.getLogger(__name__)


async def create_ticket(
    db: AsyncSession,
    user: User,
    req: CreateTicketRequest,
) -> Ticket:
    """Create a new support ticket and its initial message."""
    # Guard against spam/abusive submissions
    is_safe, reason = check_content_safety(req.subject)
    if not is_safe:
        raise InvalidInputError(reason)
    is_safe, reason = check_content_safety(req.initial_message)
    if not is_safe:
        raise InvalidInputError(reason)

    ticket = Ticket(
        user_id=user.id,
        subject=req.subject,
        category=req.category,
        priority=req.priority,
        status="open",
    )
    res = db.add(ticket)
    if inspect.isawaitable(res):
        await res
    await db.flush()

    initial_msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=user.id,
        is_staff=user.is_superuser,
        is_internal_note=False,
        content=req.initial_message,
        attachments=req.attachments,
    )
    res = db.add(initial_msg)
    if inspect.isawaitable(res):
        await res
    await db.flush()

    return ticket


async def list_user_tickets(
    db: AsyncSession,
    user: User,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TicketListItemResponse]:
    """Retrieve tickets submitted by the authenticated user."""
    query = (
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .where(Ticket.user_id == user.id)
        .order_by(Ticket.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(Ticket.status == status)

    result = await db.execute(query)
    scalars = result.scalars()
    if inspect.isawaitable(scalars):
        scalars = await scalars
    tickets = scalars.all()
    if inspect.isawaitable(tickets):
        tickets = await tickets

    items: list[TicketListItemResponse] = []
    for t in tickets:
        # Non-staff users do not count internal notes
        visible_msgs = [m for m in t.messages if not m.is_internal_note]
        items.append(
            TicketListItemResponse(
                id=t.id,
                user_id=t.user_id,
                subject=t.subject,
                category=t.category,
                priority=t.priority,
                status=t.status,
                created_at=t.created_at,
                updated_at=t.updated_at,
                messages_count=len(visible_msgs),
            )
        )
    return items


async def list_all_tickets_admin(
    db: AsyncSession,
    status: str | None = None,
    category: str | None = None,
    user_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TicketListItemResponse]:
    """Retrieve all tickets for platform staff and superusers."""
    query = (
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .order_by(Ticket.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(Ticket.status == status)
    if category:
        query = query.where(Ticket.category == category)
    if user_id:
        query = query.where(Ticket.user_id == user_id)

    result = await db.execute(query)
    scalars = result.scalars()
    if inspect.isawaitable(scalars):
        scalars = await scalars
    tickets = scalars.all()
    if inspect.isawaitable(tickets):
        tickets = await tickets

    items: list[TicketListItemResponse] = []
    for t in tickets:
        items.append(
            TicketListItemResponse(
                id=t.id,
                user_id=t.user_id,
                subject=t.subject,
                category=t.category,
                priority=t.priority,
                status=t.status,
                created_at=t.created_at,
                updated_at=t.updated_at,
                messages_count=len(t.messages),
            )
        )
    return items


async def get_ticket_detail(
    db: AsyncSession,
    ticket_id: int,
    user: User,
) -> TicketDetailResponse:
    """Fetch ticket details and threaded conversation with visibility filters."""
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if inspect.isawaitable(ticket):
        ticket = await ticket

    if not ticket:
        raise NotFoundError("تیکت مورد نظر یافت نشد.")

    # Access control: users can only view their own tickets; superusers can view all
    if not user.is_superuser and ticket.user_id != user.id:
        raise AuthorizationError("دسترسی به این تیکت مجاز نیست.")

    # Filter messages: hide internal notes from regular users
    messages = []
    for m in ticket.messages:
        if m.is_internal_note and not user.is_superuser:
            continue
        messages.append(TicketMessageResponse.model_validate(m))

    return TicketDetailResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        subject=ticket.subject,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        messages=messages,
    )


async def reply_to_ticket(
    db: AsyncSession,
    ticket_id: int,
    user: User,
    req: ReplyTicketRequest,
) -> TicketMessageResponse:
    """Add a response or staff internal note to a ticket thread."""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if inspect.isawaitable(ticket):
        ticket = await ticket

    if not ticket:
        raise NotFoundError("تیکت مورد نظر یافت نشد.")

    if not user.is_superuser and ticket.user_id != user.id:
        raise AuthorizationError("دسترسی به این تیکت مجاز نیست.")

    if ticket.status == "closed":
        raise ConflictError("این تیکت بسته شده است و امکان ارسال پاسخ وجود ندارد.")

    is_safe, reason = check_content_safety(req.content)
    if not is_safe:
        raise InvalidInputError(reason)

    # Only superusers can author internal staff notes
    is_internal = req.is_internal_note if user.is_superuser else False

    message = TicketMessage(
        ticket_id=ticket.id,
        sender_id=user.id,
        is_staff=user.is_superuser,
        is_internal_note=is_internal,
        content=req.content,
        attachments=req.attachments,
    )
    res = db.add(message)
    if inspect.isawaitable(res):
        await res

    # Transition ticket lifecycle state
    if user.is_superuser and not is_internal:
        ticket.status = "waiting_user"
    elif not user.is_superuser:
        ticket.status = "in_progress"

    await db.flush()
    return TicketMessageResponse.model_validate(message)


async def update_ticket_status(
    db: AsyncSession,
    ticket_id: int,
    user: User,
    new_status: str,
) -> Ticket:
    """Update ticket status (e.g. close ticket)."""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if inspect.isawaitable(ticket):
        ticket = await ticket

    if not ticket:
        raise NotFoundError("تیکت مورد نظر یافت نشد.")

    if not user.is_superuser and ticket.user_id != user.id:
        raise AuthorizationError("دسترسی به این تیکت مجاز نیست.")

    # Regular users may only close their tickets
    if not user.is_superuser and new_status != "closed":
        raise AuthorizationError("کاربران تنها می‌توانند تیکت خود را ببندند.")

    ticket.status = new_status
    await db.flush()
    return ticket
