"""Internal endpoints for bot communication and callbacks."""

import secrets
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import AuthenticationError
from app.internal import service
from app.internal.schemas import CallbackResponse, PublishResultCallbackRequest

router = APIRouter(tags=["internal"])
security = HTTPBearer(auto_error=False)


async def verify_worker_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Authenticate internal bot callback using shared WORKER_TOKEN."""
    if not credentials or not credentials.credentials:
        raise AuthenticationError("توکن احراز هویت سرویس ورکر ارسال نشده است.")

    settings = get_settings()
    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(credentials.credentials, settings.worker_token):
        raise AuthenticationError("توکن احراز هویت سرویس ورکر نامعتبر است.")

    return credentials.credentials


@router.post(
    "/internal/callback/publish-result",
    response_model=CallbackResponse,
    status_code=200,
)
async def publish_result_callback(
    payload: PublishResultCallbackRequest,
    _token: str = Depends(verify_worker_token),
    db: AsyncSession = Depends(get_db),
) -> CallbackResponse:
    """Receive and record publishing outcome from external bots with idempotency."""
    return await service.handle_publish_callback(db=db, payload=payload)
