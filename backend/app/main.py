"""Application entry point: creates the FastAPI app and manages its lifespan."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
    """Map rate-limit domain errors to HTTP 429."""
    return JSONResponse(status_code=429, content={"message": str(exc), "code": exc.code})

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
async def healthcheck() -> dict[str, str]:
    """Liveness probe for orchestrators and uptime monitors."""
    return {"status": "ok"}
