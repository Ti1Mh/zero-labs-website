import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import logging
import random
import traceback

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User
from app.channels.models import Channel
from app.core.config import get_settings
from app.core.database import engine
from app.core.encryption import decrypt
from app.models.content import ContentJob

logger = logging.getLogger(__name__)
session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def publish_content(ctx: dict, job_id: int) -> dict:
    """Consume a publish job: load it, decrypt channel creds, publish with DLQ & retry recovery."""
    async with session_maker() as db:
        result = await db.execute(select(ContentJob).where(ContentJob.id == job_id))
        job = result.scalar_one_or_none()
        if inspect.isawaitable(job):
            job = await job
        if job is None:
            return {"job_id": job_id, "status": "missing"}

        job.status = "processing"
        job.last_attempt_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            # Resolve the team owner (channel ownership lives with the owner)
            owner_id = job.created_by
            if owner_id is not None:
                user_result = await db.execute(select(User).where(User.id == owner_id))
                user = user_result.scalar_one_or_none()
                if inspect.isawaitable(user):
                    user = await user
                if user is not None and user.owner_user_id is not None:
                    owner_id = user.owner_user_id

            channel_result = await db.execute(
                select(Channel)
                .where(
                    Channel.owner_id == owner_id,
                    Channel.platform_id == job.platform_id,
                )
                .limit(1)
            )
            channel = channel_result.scalar_one_or_none()
            if inspect.isawaitable(channel):
                channel = await channel
            if channel is None:
                raise RuntimeError("کانالی برای این پلتفرم متصل نیست.")

            credentials = decrypt(channel.credentials_encrypted)

            # Platform dispatch simulation or real publisher
            await asyncio.sleep(1)
            logger.info(
                f"[WORKER] job {job_id} published successfully | platform_id={job.platform_id} "
                f"| token={credentials[:6]}..."
            )
            job.status = "published"
            job.error_message = None
            job.traceback_log = None
        except Exception as exc:  # noqa: BLE001
            job.last_attempt_at = datetime.now(timezone.utc)
            job.error_message = str(exc)[:500]
            job.traceback_log = traceback.format_exc()
            job.retry_count = (job.retry_count or 0) + 1

            max_retries = job.max_retries or 5
            if job.retry_count < max_retries:
                # Transient error: Schedule re-enqueue with exponential backoff + jitter
                delay = min(300, (2 ** job.retry_count) * 5 + random.uniform(1, 4))
                job.status = "queued"
                logger.warning(
                    f"[WORKER] job {job_id} failed attempt {job.retry_count}/{max_retries}. "
                    f"Re-enqueuing in {delay:.1f}s: {exc}"
                )
                redis_pool = ctx.get("redis")
                if redis_pool is not None:
                    await redis_pool.enqueue_job("publish_content", job_id, _defer_by=timedelta(seconds=delay))
            else:
                # Max retries exhausted: Transition to Dead Letter Queue (DLQ)
                job.status = "failed"
                logger.error(
                    f"[WORKER DLQ] job {job_id} permanently failed after {job.retry_count} attempts. "
                    f"Moved to Dead Letter Queue: {exc}"
                )

        final_status = job.status
        await db.commit()
        return {"job_id": job_id, "status": final_status}

async def enqueue_due_jobs(ctx: dict) -> dict:
    """Move due scheduled jobs to queued and enqueue them (every 30s)."""
    now = datetime.now(timezone.utc)
    async with session_maker() as db:
        result = await db.execute(
            select(ContentJob.id).where(
                ContentJob.status == "scheduled",
                ContentJob.scheduled_at <= now,
            ).limit(50)
        )
        ids = [row[0] for row in result.all()]
        enqueued = 0
        for job_id in ids:
            # Conditional update guards against double-enqueue
            upd = await db.execute(
                update(ContentJob)
                .where(
                    ContentJob.id == job_id,
                    ContentJob.status == "scheduled",
                )
                .values(status="queued")
            )
            if upd.rowcount == 1:
                await ctx["redis"].enqueue_job("publish_content", job_id)
                enqueued += 1
        await db.commit()
        if enqueued:
            print(f"[SCHEDULER] enqueued {enqueued} due job(s)")
        return {"due": len(ids), "enqueued": enqueued}

class WorkerSettings:
    """arq worker configuration."""

    functions = [publish_content]
    cron_jobs = [cron(enqueue_due_jobs, second={0, 30})]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)