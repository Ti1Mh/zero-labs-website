"""System prompts and formatting templates for platform-specific generation."""

from app.ai.schemas import GeneratePostRequest

PLATFORM_GUIDELINES: dict[str, str] = {
    "telegram": (
        "- Structure with a bold title, concise paragraphs, and bullet points.\n"
        "- Use relevant emojis tastefully to emphasize key ideas.\n"
        "- Format clean markdown (bold, italic, bullet lists).\n"
        "- Conclude with a clear community call-to-action."
    ),
    "instagram": (
        "- First 2 lines must be an irresistible hook before 'more...'.\n"
        "- Use readable paragraph breaks and micro-storytelling.\n"
        "- Include a question at the end to maximize comments.\n"
        "- Place 5-15 highly relevant hashtags at the bottom."
    ),
    "twitter": (
        "- Extremely punchy, intriguing, and direct.\n"
        "- Maximize signal-to-noise ratio.\n"
        "- Avoid fluff; deliver insight or strong opinion immediately.\n"
        "- Keep hashtags minimal (1-3 max)."
    ),
    "linkedin": (
        "- Professional yet human storytelling format.\n"
        "- Share actionable takeaways, business insights, or lessons learned.\n"
        "- Use generous line spacing for mobile readability.\n"
        "- Ask a thoughtful closing question for professional engagement."
    ),
    "bale": (
        "- Clean, friendly, and structured format for Iranian channel audiences.\n"
        "- Clear headline, informative body with appropriate Persian typography.\n"
        "- End with an engaging prompt or join CTA."
    ),
}

SYSTEM_PROMPT_TEMPLATE = """You are an elite social media strategist and copywriting expert for ZeroIO Labs.
Your task is to craft high-engagement, platform-tailored social media content in {language_name}.

Target Platform: {platform}
Tone: {tone}
{audience_clause}

Platform Rules:
{platform_rules}

Formatting & Quality Rules:
1. Return ONLY valid JSON matching the requested schema without conversational filler.
2. Hook must grab attention in the first 3 seconds.
3. virality_score should be a realistic estimate (0-100) based on hook strength and shareability.
4. suggested_media_prompt should describe a compelling photo, illustration, or graphic that complements the text.
"""


def build_system_prompt(request: GeneratePostRequest) -> str:
    """Build a specialized system prompt for the generation request."""
    lang_name = "Persian (Farsi)" if request.language == "fa" else "English"
    audience_clause = (
        f"Target Audience: {request.target_audience}"
        if request.target_audience
        else "Target Audience: General engaged audience interested in this niche."
    )
    platform_rules = PLATFORM_GUIDELINES.get(
        request.platform,
        PLATFORM_GUIDELINES["telegram"],
    )

    return SYSTEM_PROMPT_TEMPLATE.format(
        language_name=lang_name,
        platform=request.platform.upper(),
        tone=request.tone,
        audience_clause=audience_clause,
        platform_rules=platform_rules,
    )


def build_user_prompt(request: GeneratePostRequest) -> str:
    """Build the user message payload instructing structured output."""
    instructions = [f"Topic: {request.topic}"]
    if request.extra_instructions:
        instructions.append(f"Additional Instructions: {request.extra_instructions}")

    instructions.append("\nRespond with a JSON object containing:")
    instructions.append("- 'headline': short catchy title")
    instructions.append("- 'hook': powerful opening line")
    instructions.append("- 'body': complete formatted body")
    if request.include_hashtags:
        instructions.append("- 'hashtags': array of hashtags (starting with #)")
    else:
        instructions.append("- 'hashtags': []")
    instructions.append("- 'call_to_action': ending prompt/link")
    instructions.append("- 'virality_score': integer from 0 to 100")
    if request.include_media_prompt:
        instructions.append("- 'suggested_media_prompt': visual prompt in English for image generation")
    else:
        instructions.append("- 'suggested_media_prompt': null")

    return "\n".join(instructions)
