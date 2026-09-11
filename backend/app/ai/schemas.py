"""Pydantic schemas for AI Studio generation and model access."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

PlatformType = Literal["telegram", "instagram", "twitter", "linkedin", "bale"]
ToneType = Literal["engaging", "professional", "humorous", "educational", "bold", "storytelling", "witty"]


class GeneratePostRequest(BaseModel):
    """Input payload for generating structured social media content."""

    model_config = ConfigDict(str_strip_whitespace=True)

    topic: str = Field(..., min_length=2, max_length=1000, description="Main topic or idea of the post")
    platform: PlatformType = Field(default="telegram", description="Target social media platform")
    tone: ToneType = Field(default="engaging", description="Writing tone and persona style")
    target_audience: str | None = Field(default=None, max_length=300, description="Target demographic or reader profile")
    include_hashtags: bool = Field(default=True, description="Whether to generate trending hashtags")
    include_media_prompt: bool = Field(default=True, description="Whether to generate an image/video prompt")
    language: str = Field(default="fa", max_length=10, description="Output language code (e.g. 'fa', 'en')")
    extra_instructions: str | None = Field(default=None, max_length=500, description="Custom brand or formatting rules")


class GeneratedPostResponse(BaseModel):
    """Structured response containing all components of a generated post."""

    headline: str = Field(..., description="Short catchy headline")
    hook: str = Field(..., description="The opening line designed to capture immediate attention")
    body: str = Field(..., description="The main post body formatted specifically for the platform")
    hashtags: list[str] = Field(default_factory=list, description="List of relevant hashtags with '#' prefix")
    call_to_action: str | None = Field(default=None, description="Closing call to action (e.g. link, question)")
    virality_score: int = Field(default=85, ge=0, le=100, description="Estimated engagement potential 0-100")
    suggested_media_prompt: str | None = Field(default=None, description="Detailed prompt to generate visual asset")
    model_used: str = Field(..., description="AI model identifier that produced this content")
    provider_used: str = Field(..., description="Provider name (e.g. 'openrouter', 'mock')")


class AIModelInfo(BaseModel):
    """Metadata for an available AI model in the studio."""

    id: str
    name: str
    tier: str  # "free", "pro", "enterprise"
    description: str


class AIModelsListResponse(BaseModel):
    """Response containing models and user's tier assignment."""

    current_tier: str
    assigned_model: str
    models: list[AIModelInfo]
