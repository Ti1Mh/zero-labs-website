"""FastAPI router for Support Desk tickets and threaded messages."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_superuser
from app.auth.models import User
from app.core.database import get_db
from app.support.schemas import (
    CreateTicketRequest,
    ReplyTicketRequest,
    TicketDetailResponse,
    TicketListItemResponse,
    TicketMessageResponse,
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

router = APIRouter(
    prefix="/support",
    tags=["Support & Ticketing"],
)


# --- User Ticket Endpoints ---

@router.post("/tickets", response_model=TicketDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket_endpoint(
    request: CreateTicketRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit a new customer support ticket with initial message."""
    ticket = await create_ticket(db, current_user, request)
    return await get_ticket_detail(db, ticket.id, current_user)


@router.get("/tickets", response_model=list[TicketListItemResponse])
async def list_user_tickets_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List tickets opened by the authenticated user."""
    return await list_user_tickets(db, current_user, status=status_filter, limit=limit, offset=offset)


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket_endpoint(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get full ticket details and discussion thread (internal staff notes excluded for users)."""
    return await get_ticket_detail(db, ticket_id, current_user)


@router.post("/tickets/{ticket_id}/reply", response_model=TicketMessageResponse)
async def reply_ticket_endpoint(
    ticket_id: int,
    request: ReplyTicketRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a reply to an ongoing ticket thread."""
    return await reply_to_ticket(db, ticket_id, current_user, request)


@router.post("/tickets/{ticket_id}/close", response_model=TicketDetailResponse)
async def close_ticket_endpoint(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Close an ongoing ticket."""
    await update_ticket_status(db, ticket_id, current_user, new_status="closed")
    return await get_ticket_detail(db, ticket_id, current_user)


# --- Super Admin Support Desk Endpoints ---

@router.get("/admin/tickets", response_model=list[TicketListItemResponse])
async def list_all_tickets_admin_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Super Admin oversight: list all customer tickets across users and categories."""
    return await list_all_tickets_admin(
        db, status=status_filter, category=category, user_id=user_id, limit=limit, offset=offset
    )


@router.patch("/admin/tickets/{ticket_id}/status", response_model=TicketDetailResponse)
async def admin_update_ticket_status_endpoint(
    ticket_id: int,
    request: UpdateTicketStatusRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Super Admin: change status of any ticket (open, in_progress, waiting_user, closed)."""
    await update_ticket_status(db, ticket_id, admin, new_status=request.status)
    return await get_ticket_detail(db, ticket_id, admin)


@router.post("/admin/tickets/{ticket_id}/reply", response_model=TicketMessageResponse)
async def admin_reply_ticket_endpoint(
    ticket_id: int,
    request: ReplyTicketRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Super Admin reply: supports both public responses and internal staff-only notes."""
    return await reply_to_ticket(db, ticket_id, admin, request)
