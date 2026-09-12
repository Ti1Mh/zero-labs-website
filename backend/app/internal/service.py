"""Service logic for processing callbacks from external bots."""

from datetime import datetime, timezone
import inspect
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.internal.schemas import CallbackResponse, PublishResultCallbackRequest
from app.models.callback import ProcessedCallbackEvent
from app.models.content import ContentJob

logger = logging.getLogger(__name__)


async def handle_publish_callback(
    db: AsyncSession,
    payload: PublishResultCallbackRequest,
) -> CallbackResponse:
    """Idempotently process an external bot's publishing result callback."""
    # 1. Check Idempotency: Has this event already been processed?
    existing_event_result = await db.execute(
        select(ProcessedCallbackEvent).where(ProcessedCallbackEvent.event_id == payload.event_id)
    )
    existing_event = existing_event_result.scalar_one_or_none()
    if inspect.isawaitable(existing_event):
        existing_event = await existing_event

    if existing_event is not None:
        logger.info(
            "Duplicate callback received for event_id=%s, job_id=%d. Ignoring idempotent replay.",
            payload.event_id,
            payload.job_id,
        )
        return CallbackResponse(
            status="duplicate_ignored",
            job_id=payload.job_id,
            event_id=payload.event_id,
            message="Event already processed.",
        )

    # 2. Find the target ContentJob
    job_result = await db.execute(
        select(ContentJob).where(ContentJob.id == payload.job_id)
    )
    job = job_result.scalar_one_or_none()
    if inspect.isawaitable(job):
        job = await job

    if job is None:
        logger.error("Callback received for non-existent job_id=%d", payload.job_id)
        raise NotFoundError(f"جاب با شناسه {payload.job_id} یافت نشد.")

    # 3. Update ContentJob state
    job.status = payload.status
    job.last_attempt_at = datetime.now(timezone.utc)

    # Store platform_post_id in extra_metadata if provided
    metadata = dict(job.extra_metadata or {})
    if payload.platform_post_id:
        metadata["platform_post_id"] = payload.platform_post_id
    job.extra_metadata = metadata

    if payload.status == "published":
        job.error_message = None
        job.traceback_log = None
    else:
        job.error_message = payload.error_message
        job.retry_count = (job.retry_count or 0) + 1

    # 4. Record event in processed_callback_events table
    callback_record = ProcessedCallbackEvent(
        event_id=payload.event_id,
        job_id=payload.job_id,
        status=payload.status,
        platform_post_id=payload.platform_post_id,
        error_message=payload.error_message,
    )
    res = db.add(callback_record)
    if inspect.isawaitable(res):
        await res

    await db.flush()

    logger.info(
        "Callback successfully processed for job_id=%d: status=%s | event_id=%s",
        payload.job_id,
        payload.status,
        payload.event_id,
    )

    return CallbackResponse(
        status="processed",
        job_id=payload.job_id,
        event_id=payload.event_id,
        message="Job status updated successfully.",
    )
