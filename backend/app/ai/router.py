"""FastAPI endpoints for AI Studio content generation and streaming."""

from collections.abc import AsyncIterator
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import (
    AIModelsListResponse,
    GeneratePostRequest,
    GeneratedPostResponse,
)
from app.ai.service import (
    generate_post,
    list_available_models,
    stream_post,
)
from app.auth.dependencies import TeamContext, require
from app.core.database import get_db

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/generate", response_model=GeneratedPostResponse)
async def generate_post_endpoint(
    request: GeneratePostRequest,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> GeneratedPostResponse:
    """Generate structured social media content tailored to platform and user tier."""
    return await generate_post(db, team, request)


@router.post("/stream")
async def stream_post_endpoint(
    request: GeneratePostRequest,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream token deltas in real-time using Server-Sent Events (SSE)."""

    async def event_generator() -> AsyncIterator[str]:
        try:
            async for token in stream_post(db, team, request):
                data = json.dumps({"content": token}, ensure_ascii=False)
                yield f"event: chunk\ndata: {data}\n\n"
            yield "event: done\ndata: [DONE]\n\n"
        except Exception as exc:
            error_data = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models", response_model=AIModelsListResponse)
async def get_models_endpoint(
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> AIModelsListResponse:
    """Return available AI models and current assigned tier for the user."""
    return await list_available_models(db, team)
