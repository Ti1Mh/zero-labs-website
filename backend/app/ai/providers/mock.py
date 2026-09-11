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

        # Content Doctor response fields
        if "overall_score" in fields:
            mock_data["overall_score"] = 86
        if "hook_score" in fields:
            mock_data["hook_score"] = 82
        if "readability_score" in fields:
            mock_data["readability_score"] = 90
        if "call_to_action_score" in fields:
            mock_data["call_to_action_score"] = 85
        if "strengths" in fields:
            mock_data["strengths"] = ["ساختار بندی خوب با ایموجی", "وضوح پیام اصلی برای مخاطب"]
        if "weaknesses" in fields:
            mock_data["weaknesses"] = ["هوک شروع می‌توانست کنجکاوی بیشتری برانگیزد", "دعوت به اقدام می‌تواند صریح‌تر باشد"]
        if "improved_version" in fields:
            mock_data["improved_version"] = f"✨ نسخه بهینه‌سازی‌شده:\n\n{prompt[:120]}...\n\nبهترین نتیجه در تداوم است."
        if "alternative_hooks" in fields:
            mock_data["alternative_hooks"] = [
                "راز واقعی این موضوع چیست؟ 👇",
                "۳ اشتباهی که ۹۰ درصد افراد مرتکب می‌شوند:",
                "اگر می‌خواهید در این زمینه پیشرفت کنید، این پست برای شماست:",
            ]

        # Repurposing fields
        if "source_summary" in fields:
            mock_data["source_summary"] = f"خلاصه پیام اصلی: {prompt[:60]}..."
        if "posts" in fields:
            from app.ai.schemas import RepurposedPlatformPost
            sample_variant = RepurposedPlatformPost(
                headline=f"پست اختصاصی برای {prompt[:25]}",
                hook="نکته مهم و کلیدی 👇",
                body=f"محتوای بهینه‌سازی‌شده بر اساس لحن برند و فرمت پلتفرم: {prompt[:80]}",
                hashtags=["#تخصصی", "#رشد"],
                call_to_action="نظرتان را کامنت کنید.",
            )
            mock_data["posts"] = {
                "telegram": sample_variant,
                "instagram": sample_variant,
                "twitter": sample_variant,
            }

        if "model_used" in fields:
            mock_data["model_used"] = model
        if "provider_used" in fields:
            mock_data["provider_used"] = self.provider_name

        return response_model.model_validate(mock_data)
