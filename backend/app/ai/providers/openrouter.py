"""OpenRouter provider implementation utilizing the OpenAI-compatible chat API."""

from collections.abc import AsyncIterator
import json
import logging
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.providers.base import BaseAIProvider
from app.core.exceptions import InvalidInputError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenRouterProvider(BaseAIProvider):
    """Client for OpenRouter API supporting streaming, model fallbacks, and structured JSON."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str = "https://zeroio.io",
        app_name: str = "ZeroIO Social Studio",
        timeout: float = 35.0,
        fallback_models: list[str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.site_url = site_url
        self.app_name = app_name
        self.timeout = timeout
        self.fallback_models = fallback_models or ["openai/gpt-4o-mini"]

    def _get_headers(self) -> dict[str, str]:
        """Build OpenRouter required and metadata headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
            "Content-Type": "application/json",
        }

    def _build_messages(self, prompt: str, system_prompt: str | None = None) -> list[dict[str, str]]:
        """Construct the OpenAI chat completions message list."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def generate_text(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Execute text generation against OpenRouter."""
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "models": [model] + [m for m in self.fallback_models if m != model],
            "messages": self._build_messages(prompt, system_prompt),
            "temperature": temperature,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(endpoint, headers=self._get_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()

    async def stream_text(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream response tokens using server-sent events."""
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "models": [model] + [m for m in self.fallback_models if m != model],
            "messages": self._build_messages(prompt, system_prompt),
            "temperature": temperature,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", endpoint, headers=self._get_headers(), json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            logger.debug("Failed to decode SSE chunk: %s", data_str)

    async def generate_structured(
        self,
        prompt: str,
        model: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> T:
        """Generate structured response validated against a Pydantic schema."""
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "models": [model] + [m for m in self.fallback_models if m != model],
            "messages": self._build_messages(prompt, system_prompt),
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(endpoint, headers=self._get_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Clean markdown JSON block formatting if present
            cleaned_content = re.sub(r"^```json\s*|\s*```$", "", content, flags=re.MULTILINE).strip()

            try:
                parsed_dict = json.loads(cleaned_content)
            except json.JSONDecodeError as exc:
                logger.error("OpenRouter response was not valid JSON: %s", content)
                raise InvalidInputError("پاسخ هوش مصنوعی به فرمت معتبر JSON نبود.") from exc

            # Inject model and provider metadata if needed
            if "model_used" in response_model.model_fields and "model_used" not in parsed_dict:
                parsed_dict["model_used"] = model
            if "provider_used" in response_model.model_fields and "provider_used" not in parsed_dict:
                parsed_dict["provider_used"] = "openrouter"

            try:
                return response_model.model_validate(parsed_dict)
            except ValidationError as exc:
                logger.error("Structured validation error: %s | data: %s", exc, parsed_dict)
                raise InvalidInputError("ساختار دیتای دریافتی از هوش مصنوعی ناقص بود.") from exc
