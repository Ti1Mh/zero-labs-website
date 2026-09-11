"""Deterministic Mock AI Provider for testing and local development without API keys."""

import asyncio
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from app.ai.providers.base import BaseAIProvider

T = TypeVar("T", bound=BaseModel)


class MockAIProvider(BaseAIProvider):
    """Deterministic provider that simulates LLM generation without network requests."""

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    async def generate_text(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Simulate single text generation."""
        return f"[MOCK:{model}] پاسخ آزمایشی برای پرامپت: {prompt[:40]}..."

    async def stream_text(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Simulate real-time streaming tokens."""
        tokens = [
            "🎯 ",
            "عنوان: ",
            f"بررسی جامع {prompt[:25]}...\n\n",
            "متن پست: ",
            "این یک متن شبیه‌سازی‌شده است ",
            "که به صورت استریم زنده ",
            "از طریق سرور برای فرانت‌اند ارسال می‌شود. ",
            "\n\n#هوش_مصنوعی #محتوا",
        ]
        for token in tokens:
            await asyncio.sleep(0.01)
            yield token

    async def generate_structured(
        self,
        prompt: str,
        model: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> T:
        """Simulate structured schema output."""
        # Check if fields match GeneratedPostResponse schema
        fields = response_model.model_fields.keys()
        mock_data: dict = {}

        if "headline" in fields:
            mock_data["headline"] = f"راهنمای کاربردی: {prompt[:30]}"
        if "hook" in fields:
            mock_data["hook"] = f"چرا اکثر افراد در {prompt[:25]} اشتباه می‌کنند؟ راز موفقیت اینجاست 👇"
        if "body" in fields:
            mock_data["body"] = (
                f"اگر به دنبال تحول در {prompt[:30]} هستید، این ۳ نکته اساسی را به یاد داشته باشید:\n\n"
                "۱. استمرار در انتشار محتوا\n"
                "۲. شخصی‌سازی بر اساس لحن برند\n"
                "۳. بررسی نرخ تعامل و فیدبک مخاطبان"
            )
        if "hashtags" in fields:
            mock_data["hashtags"] = ["#محتوا", "#کسب_و_کار", "#رشد", "#تکنولوژی"]
        if "call_to_action" in fields:
            mock_data["call_to_action"] = "شما از چه روشی استفاده می‌کنید؟ نظرتان را برای ما بنویسید."
        if "virality_score" in fields:
            mock_data["virality_score"] = 88
        if "suggested_media_prompt" in fields:
            mock_data["suggested_media_prompt"] = (
                f"Professional high-res minimalist 3D isometric rendering depicting {prompt[:30]}, modern aesthetic"
            )
        if "model_used" in fields:
            mock_data["model_used"] = model
        if "provider_used" in fields:
            mock_data["provider_used"] = self.provider_name

        return response_model.model_validate(mock_data)
