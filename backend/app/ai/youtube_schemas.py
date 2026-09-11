"""Pydantic schemas and deterministic guards for YouTube character limits and COPPA."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class YouTubePostContent(BaseModel):
    """Structured content conforming to YouTube metadata limits."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., min_length=1, max_length=100, description="YouTube video title (max 100 chars)")
    description: str = Field(..., min_length=1, max_length=5000, description="Video description (max 5000 chars)")
    tags: list[str] = Field(default_factory=list, description="Comma-separated keywords (max 500 chars total)")
    is_short: bool = Field(default=False, description="Whether this video is a vertical Short (<=60-180s)")
    made_for_kids: bool = Field(default=False, description="COPPA compliance declaration")
    call_to_action: str | None = Field(default=None, description="Closing CTA (e.g. subscribe, link in bio)")
    suggested_media_prompt: str | None = Field(default=None, description="Visual description for thumbnail / asset")

    @field_validator("tags")
    @classmethod
    def validate_total_tags_length(cls, tags: list[str]) -> list[str]:
        """Ensure the combined comma-separated length of all tags does not exceed 500 chars."""
        cleaned_tags = [t.strip().lstrip("#") for t in tags if t.strip()]
        total_len = sum(len(t) for t in cleaned_tags) + max(0, len(cleaned_tags) - 1)
        if total_len <= 500:
            return cleaned_tags

        # Smart truncation: accumulate tags until the 500 character ceiling is reached
        accumulated = 0
        valid_tags: list[str] = []
        for tag in cleaned_tags:
            tag_cost = len(tag) + (1 if valid_tags else 0)
            if accumulated + tag_cost <= 500:
                valid_tags.append(tag)
                accumulated += tag_cost
            else:
                break
        return valid_tags


def enforce_youtube_limits(content: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback to truncate text on word boundaries if an LLM exceeds limits."""
    result = dict(content)

    # 1. Enforce Title <= 100 chars
    title = str(result.get("title", "")).strip()
    if len(title) > 100:
        truncated = title[:97]
        last_space = truncated.rfind(" ")
        if last_space > 60:
            truncated = truncated[:last_space]
        result["title"] = f"{truncated}..."
    else:
        result["title"] = title

    # 2. Enforce Description <= 5000 chars
    description = str(result.get("description", "")).strip()
    if len(description) > 5000:
        result["description"] = description[:4997] + "..."
    else:
        result["description"] = description

    # 3. Enforce Tags total <= 500 chars
    raw_tags = result.get("tags") or []
    if isinstance(raw_tags, list):
        cleaned_tags = [str(t).strip().lstrip("#") for t in raw_tags if str(t).strip()]
        accumulated = 0
        valid_tags = []
        for t in cleaned_tags:
            tag_cost = len(t) + (1 if valid_tags else 0)
            if accumulated + tag_cost <= 500:
                valid_tags.append(t)
                accumulated += tag_cost
            else:
                break
        result["tags"] = valid_tags

    # 4. If it's a YouTube Short, ensure #Shorts is present in title or description
    is_short = result.get("is_short", False)
    if is_short:
        desc = result.get("description", "")
        if "#Shorts" not in desc and "#shorts" not in desc:
            result["description"] = f"{desc}\n\n#Shorts".strip()

    return result
