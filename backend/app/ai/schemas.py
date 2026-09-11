"""Pydantic schemas for AI Studio, Brand Voice Personas, Content Doctor, and Analytics integration."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

PlatformType = Literal["telegram", "instagram", "twitter", "linkedin", "bale"]
ToneType = Literal["engaging", "professional", "humorous", "educational", "bold", "storytelling", "witty"]


# --- Brand Persona Schemas ---

class ToneTraitsSchema(BaseModel):
    """Numerical ratings (1-5) for stylistic axes."""

    formality: int = Field(default=3, ge=1, le=5, description="1=Very casual, 5=Highly formal")
    humor: int = Field(default=2, ge=1, le=5, description="1=Serious, 5=Playful/Witty")
    enthusiasm: int = Field(default=4, ge=1, le=5, description="1=Calm/Dry, 5=Energetic/Hyped")
    boldness: int = Field(default=3, ge=1, le=5, description="1=Diplomatic, 5=Provocative/Direct")


class CreateBrandPersonaRequest(BaseModel):
    """Payload to create a new Brand Persona profile."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=2, max_length=100, description="Name of the brand persona")
    description: str | None = Field(default=None, max_length=1000, description="Overview of the business and mission")
    tone_traits: ToneTraitsSchema = Field(default_factory=ToneTraitsSchema)
    forbidden_words: list[str] = Field(default_factory=list, description="List of words never to be used in posts")
    signature_phrases: list[str] = Field(default_factory=list, description="Taglines, slogans, or catchphrases to include")
    sample_posts: list[str] = Field(default_factory=list, max_length=10, description="Few-shot historical top-performing posts")
    target_audience: str | None = Field(default=None, max_length=300, description="Core target audience demographic")
    is_default: bool = Field(default=False, description="Whether this persona is the default for generation")


class UpdateBrandPersonaRequest(BaseModel):
    """Payload to update an existing Brand Persona profile."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = None
    tone_traits: ToneTraitsSchema | None = None
    forbidden_words: list[str] | None = None
    signature_phrases: list[str] | None = None
    sample_posts: list[str] | None = None
    target_audience: str | None = None
    is_default: bool | None = None


class BrandPersonaResponse(BaseModel):
    """Public representation of a Brand Persona."""

    id: int
    owner_id: int
    name: str
    description: str | None
    tone_traits: dict
    forbidden_words: list[str]
    signature_phrases: list[str]
    sample_posts: list[str]
    target_audience: str | None
    is_default: bool
    created_at: datetime
    updated_at: datetime


# --- Generation Schemas ---

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
    persona_id: int | None = Field(default=None, description="Optional Brand Persona ID to enforce voice constraints")
    use_analytics_context: bool = Field(default=True, description="Inject historical top-performing post patterns")


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


# --- Content Doctor & Optimization Schemas ---

class OptimizePostRequest(BaseModel):
    """Payload to submit an existing draft for scoring and AI revision."""

    model_config = ConfigDict(str_strip_whitespace=True)

    draft_text: str = Field(..., min_length=10, max_length=5000, description="The draft post to analyze and improve")
    platform: PlatformType = Field(default="telegram", description="Target platform")
    persona_id: int | None = Field(default=None, description="Optional persona profile to match tone against")
    extra_instructions: str | None = Field(default=None, max_length=500)


class OptimizePostResponse(BaseModel):
    """AI critique, engagement scoring, and improved rewrite."""

    overall_score: int = Field(..., ge=0, le=100, description="Overall post quality and engagement index")
    hook_score: int = Field(..., ge=0, le=100, description="First-3-seconds retention effectiveness")
    readability_score: int = Field(..., ge=0, le=100, description="Structure, spacing, and mobile clarity")
    call_to_action_score: int = Field(..., ge=0, le=100, description="Likelihood of reader taking action/commenting")
    strengths: list[str] = Field(default_factory=list, description="Key strong points of the current draft")
    weaknesses: list[str] = Field(default_factory=list, description="Identified flaws or missed opportunities")
    improved_version: str = Field(..., description="Fully polished rewrite ready for publishing")
    alternative_hooks: list[str] = Field(default_factory=list, description="3 high-converting hook alternatives")
    model_used: str = Field(default="mock", description="Model that performed the optimization")


# --- Smart Scheduling Schemas ---

class SmartScheduleRequest(BaseModel):
    """Payload to request optimal scheduling windows."""

    model_config = ConfigDict(str_strip_whitespace=True)

    platform: PlatformType = Field(default="telegram", description="Target platform")
    topic: str | None = Field(default=None, description="Optional post topic or theme")


class ScheduleSlot(BaseModel):
    """A recommended publication timestamp backed by reasoning."""

    recommended_time: datetime
    day_name: str
    confidence_score: int = Field(ge=0, le=100)
    reasoning: str


class SmartScheduleResponse(BaseModel):
    """AI recommended posting schedule based on channel analytics history."""

    platform: str
    recommended_slots: list[ScheduleSlot]
    analytics_insight: str


# --- Multi-Platform Repurposing Schemas ---

class RepurposeRequest(BaseModel):
    """Payload to adapt a single core idea/text into multiple platform formats."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_text: str = Field(..., min_length=10, max_length=5000, description="Source article, notes, or master post")
    target_platforms: list[PlatformType] = Field(
        default=["telegram", "instagram", "twitter", "linkedin", "bale"],
        min_length=1,
        max_length=5,
    )
    persona_id: int | None = None
    extra_instructions: str | None = None


class RepurposedPlatformPost(BaseModel):
    """Single platform variant in a repurpose response."""

    headline: str
    hook: str
    body: str
    hashtags: list[str]
    call_to_action: str | None = None


class RepurposeResponse(BaseModel):
    """Multi-platform adapted versions of the source content."""

    source_summary: str
    posts: dict[str, RepurposedPlatformPost]
    model_used: str
