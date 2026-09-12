"""Application entry point: creates the FastAPI app and manages its lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import inspect

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from arq import create_pool
from arq.connections import RedisSettings

from app.api.v1.content import router as content_router
from app.auth.router import router as auth_router
from app.analytics.router import router as analytics_router
from app.subscriptions.router import router as subscriptions_router
from app.channels.router import router as channel_router
from app.uploads.router import router as uploads_router
from app.ai.router import router as ai_router
from app.admin.router import router as admin_router
from app.support.router import router as support_router
from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    AuthorizationError,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Schema is managed by Alembic (run `alembic upgrade head`)."""
    app.state.redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    yield
    await app.state.redis.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(InvalidInputError)
async def handle_invalid_input(request: Request, exc: InvalidInputError) -> JSONResponse:
    """Map invalid-input domain errors to HTTP 400."""
    return JSONResponse(status_code=400, content={"message": str(exc), "code": exc.code})


@app.exception_handler(AuthenticationError)
async def handle_authentication(request: Request, exc: AuthenticationError) -> JSONResponse:
    """Map authentication domain errors to HTTP 401."""
    return JSONResponse(status_code=401, content={"message": str(exc), "code": exc.code})

@app.exception_handler(AuthorizationError)
async def handle_forbidden(request: Request, exc: AuthorizationError) -> JSONResponse:
    """Map authorization domain errors to HTTP 403."""
    return JSONResponse(status_code=403,content={"message": str(exc), "code": exc.code})


@app.exception_handler(NotFoundError)
async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    """Map not-found domain errors to HTTP 404."""
    return JSONResponse(status_code=404, content={"message": str(exc), "code": exc.code})


@app.exception_handler(ConflictError)
async def handle_conflict(request: Request, exc: ConflictError) -> JSONResponse:
    """Map conflict domain errors to HTTP 409."""
    return JSONResponse(status_code=409, content={"message": str(exc), "code": exc.code})


@app.exception_handler(RateLimitError)
async def handle_rate_limit(request: Request, exc: RateLimitError) -> JSONResponse:
    """Map rate-limit domain errors to HTTP 429 with standard Retry-After header."""
    headers: dict[str, str] = {}
    if getattr(exc, "retry_after", None) is not None:
        headers["Retry-After"] = str(exc.retry_after)
    content: dict[str, object] = {"message": str(exc), "code": exc.code}
    if getattr(exc, "retry_after", None) is not None:
        content["retry_after"] = exc.retry_after
    return JSONResponse(status_code=429, content=content, headers=headers)


app.include_router(auth_router, prefix="/api/v1")
app.include_router(content_router, prefix="/api/v1")
app.include_router(channel_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(subscriptions_router, prefix="/api/v1")
app.include_router(uploads_router, prefix="/api/v1")
app.include_router(ai_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(support_router, prefix="/api/v1")

@app.get("/healthz", tags=["meta"])
async def healthcheck(request: Request) -> JSONResponse:
    """Liveness and readiness probe probing DB and Redis status."""
    db_status = "ok"
    redis_status = "ok"
    is_healthy = True

    # 1. Database probe
    try:
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"unhealthy: {exc}"
        is_healthy = False

    # 2. Redis probe
    try:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is not None:
            ping_res = redis_client.ping()
            if inspect.isawaitable(ping_res):
                await ping_res
        else:
            redis_status = "unconfigured"
    except Exception as exc:
        redis_status = f"unhealthy: {exc}"
        is_healthy = False

    status_code = 200 if is_healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if is_healthy else "degraded",
            "database": db_status,
            "redis": redis_status,
        },
    )
