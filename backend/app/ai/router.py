"""FastAPI endpoints for AI Studio: Generation, Personas, Doctor, and Scheduling."""

from collections.abc import AsyncIterator
import json
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import (
    AIModelsListResponse,
    BrandPersonaResponse,
    CreateBrandPersonaRequest,
    GeneratePostRequest,
    GeneratedPostResponse,
    OptimizePostRequest,
    OptimizePostResponse,
    RepurposeRequest,
    RepurposeResponse,
    SmartScheduleRequest,
    SmartScheduleResponse,
    UpdateBrandPersonaRequest,
)
from app.ai.service import (
    create_brand_persona,
    delete_brand_persona,
    generate_post,
    get_brand_persona,
    list_available_models,
    list_brand_personas,
    optimize_post,
    recommend_smart_schedule,
    repurpose_post,
    set_default_brand_persona,
    stream_post,
    update_brand_persona,
)
from app.ai.dependencies import enforce_ai_guardrails
from app.auth.dependencies import TeamContext, require
from app.core.database import get_db

router = APIRouter(prefix="/ai", tags=["ai"])


# --- Generation & Models Endpoints ---

@router.post("/generate", response_model=GeneratedPostResponse)
async def generate_post_endpoint(
    request: GeneratePostRequest,
    team: TeamContext = Depends(enforce_ai_guardrails),
    db: AsyncSession = Depends(get_db),
) -> GeneratedPostResponse:
    """Generate structured social media content tailored to platform, persona, and user tier."""
    return await generate_post(db, team, request)


@router.post("/stream")
async def stream_post_endpoint(
    request: GeneratePostRequest,
    team: TeamContext = Depends(enforce_ai_guardrails),
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


# --- Brand Persona Endpoints ---

@router.post("/personas", response_model=BrandPersonaResponse, status_code=status.HTTP_201_CREATED)
async def create_persona_endpoint(
    request: CreateBrandPersonaRequest,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> BrandPersonaResponse:
    """Create a new Brand Persona profile."""
    persona = await create_brand_persona(db, team, request)
    return BrandPersonaResponse.model_validate(persona, from_attributes=True)


@router.get("/personas", response_model=list[BrandPersonaResponse])
async def list_personas_endpoint(
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> list[BrandPersonaResponse]:
    """List all Brand Personas for the team."""
    personas = await list_brand_personas(db, team)
    return [BrandPersonaResponse.model_validate(p, from_attributes=True) for p in personas]


@router.get("/personas/{persona_id}", response_model=BrandPersonaResponse)
async def get_persona_endpoint(
    persona_id: int,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> BrandPersonaResponse:
    """Get a single Brand Persona by ID."""
    persona = await get_brand_persona(db, team, persona_id)
    return BrandPersonaResponse.model_validate(persona, from_attributes=True)


@router.put("/personas/{persona_id}", response_model=BrandPersonaResponse)
async def update_persona_endpoint(
    persona_id: int,
    request: UpdateBrandPersonaRequest,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> BrandPersonaResponse:
    """Update a Brand Persona profile."""
    persona = await update_brand_persona(db, team, persona_id, request)
    return BrandPersonaResponse.model_validate(persona, from_attributes=True)


@router.delete("/personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona_endpoint(
    persona_id: int,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a Brand Persona."""
    await delete_brand_persona(db, team, persona_id)


@router.post("/personas/{persona_id}/default", response_model=BrandPersonaResponse)
async def set_default_persona_endpoint(
    persona_id: int,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> BrandPersonaResponse:
    """Set a specific persona as the team's default for future generations."""
    persona = await set_default_brand_persona(db, team, persona_id)
    return BrandPersonaResponse.model_validate(persona, from_attributes=True)


# --- Content Doctor & Advanced Intelligence Endpoints ---

@router.post("/optimize", response_model=OptimizePostResponse)
async def optimize_post_endpoint(
    request: OptimizePostRequest,
    team: TeamContext = Depends(enforce_ai_guardrails),
    db: AsyncSession = Depends(get_db),
) -> OptimizePostResponse:
    """Audit and polish an existing draft post ('Content Doctor')."""
    return await optimize_post(db, team, request)


@router.post("/repurpose", response_model=RepurposeResponse)
async def repurpose_post_endpoint(
    request: RepurposeRequest,
    team: TeamContext = Depends(enforce_ai_guardrails),
    db: AsyncSession = Depends(get_db),
) -> RepurposeResponse:
    """Repurpose a master idea or text into platform-specific posts across multiple channels."""
    return await repurpose_post(db, team, request)


@router.post("/smart-schedule", response_model=SmartScheduleResponse)
async def smart_schedule_endpoint(
    request: SmartScheduleRequest,
    team: TeamContext = Depends(enforce_ai_guardrails),
    db: AsyncSession = Depends(get_db),
) -> SmartScheduleResponse:
    """Get AI-recommended scheduling time slots based on past performance metrics."""
    return await recommend_smart_schedule(db, team, request)
