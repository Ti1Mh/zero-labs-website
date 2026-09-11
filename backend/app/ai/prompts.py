"""System prompts and formatting templates for platform-specific generation, personas, and doctoring."""

from typing import Any
from app.ai.schemas import GeneratePostRequest, OptimizePostRequest, RepurposeRequest

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
    "youtube": (
        "- Title: High-CTR, compelling, strictly under 100 characters (recommended 60-80 chars).\n"
        "- Hook: First 3 seconds of description/script must create curiosity.\n"
        "- Description: Detailed SEO-rich overview with chapters/summary (under 5000 characters).\n"
        "- Tags: Comma-separated search keywords; total combined characters must be under 500.\n"
        "- Shorts: If creating vertical video content, include #Shorts in title and description.\n"
        "- Call-to-Action: Ask viewers to subscribe, comment, and check links."
    ),
}

SYSTEM_PROMPT_TEMPLATE = """You are an elite social media strategist and copywriting expert for ZeroIO Labs.
Your task is to craft high-engagement, platform-tailored social media content in {language_name}.

Target Platform: {platform}
Tone: {tone}
{audience_clause}

Platform Rules:
{platform_rules}

{persona_section}

{analytics_section}

Formatting & Quality Rules:
1. Return ONLY valid JSON matching the requested schema without conversational filler.
2. Hook must grab attention in the first 3 seconds.
3. virality_score should be a realistic estimate (0-100) based on hook strength and shareability.
4. suggested_media_prompt should describe a compelling photo, illustration, or graphic that complements the text.
"""


def _format_persona_clause(persona: Any | None) -> str:
    """Format Brand Persona constraints including tone sliders, lexicons, and exemplars."""
    if not persona:
        return ""

    lines = ["Brand Voice & Persona Requirements:"]
    lines.append(f"- Brand Name: {persona.name}")
    if persona.description:
        lines.append(f"- Brand Overview: {persona.description}")

    if hasattr(persona, "tone_traits") and isinstance(persona.tone_traits, dict) and persona.tone_traits:
        traits = persona.tone_traits
        lines.append(
            f"- Stylistic Sliders (1-5 scale): Formality={traits.get('formality', 3)}/5, "
            f"Humor={traits.get('humor', 2)}/5, Enthusiasm={traits.get('enthusiasm', 4)}/5, "
            f"Boldness={traits.get('boldness', 3)}/5"
        )

    if hasattr(persona, "forbidden_words") and isinstance(persona.forbidden_words, list) and persona.forbidden_words:
        forbidden_str = ", ".join(f'"{w}"' for w in persona.forbidden_words)
        lines.append(f"- STRICT FORBIDDEN WORDS (Never use these): {forbidden_str}")

    if hasattr(persona, "signature_phrases") and isinstance(persona.signature_phrases, list) and persona.signature_phrases:
        sig_str = ", ".join(f'"{s}"' for s in persona.signature_phrases)
        lines.append(f"- Signature Phrases / Catchphrases to echo: {sig_str}")

    if hasattr(persona, "sample_posts") and isinstance(persona.sample_posts, list) and persona.sample_posts:
        lines.append("- Exemplary Past Brand Posts (Mimic their rhythm, emoji style, and voice):")
        for idx, sample in enumerate(persona.sample_posts[:3], 1):
            lines.append(f"  Example {idx}: \"\"\"{sample}\"\"\"")

    return "\n".join(lines)


def _format_analytics_clause(past_winners: list[str] | None) -> str:
    """Format analytics feedback loop with historical successful posts."""
    if not past_winners:
        return ""

    lines = ["Historical Top-Performing Posts for This Channel:"]
    lines.append("Analyze these past winning posts and replicate their engagement patterns:")
    for idx, post in enumerate(past_winners[:3], 1):
        lines.append(f"  Winning Post {idx}: \"\"\"{post}\"\"\"")
    return "\n".join(lines)


def build_system_prompt(
    request: GeneratePostRequest,
    persona: Any | None = None,
    past_winners: list[str] | None = None,
) -> str:
    """Build a specialized system prompt for the generation request."""
    lang_name = "Persian (Farsi)" if request.language == "fa" else "English"
    audience_clause = (
        f"Target Audience: {request.target_audience or getattr(persona, 'target_audience', None)}"
        if (request.target_audience or getattr(persona, "target_audience", None))
        else "Target Audience: General engaged audience interested in this niche."
    )
    platform_rules = PLATFORM_GUIDELINES.get(
        request.platform,
        PLATFORM_GUIDELINES["telegram"],
    )

    persona_section = _format_persona_clause(persona)
    analytics_section = _format_analytics_clause(past_winners)

    return SYSTEM_PROMPT_TEMPLATE.format(
        language_name=lang_name,
        platform=request.platform.upper(),
        tone=request.tone,
        audience_clause=audience_clause,
        platform_rules=platform_rules,
        persona_section=persona_section,
        analytics_section=analytics_section,
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


# --- Content Doctor Prompts ---

DOCTOR_SYSTEM_PROMPT = """You are the Senior Content Doctor and Chief Engagement Auditor for ZeroIO Labs.
Your mission is to perform a surgical review of a social media draft for {platform} and return an improved, high-converting rewrite.

{persona_section}

Platform Rules:
{platform_rules}

Evaluation Criteria:
1. 'hook_score' (0-100): Will it stop the user from scrolling in the first 2 seconds?
2. 'readability_score' (0-100): Are sentences digestible? Is mobile spacing used properly?
3. 'call_to_action_score' (0-100): Does it compel readers to comment, share, or click?
4. 'overall_score' (0-100): Composite index.
5. 'strengths': 2-3 genuine strong points.
6. 'weaknesses': 2-3 clear areas for improvement.
7. 'improved_version': The complete rewritten, polished post ready for publishing.
8. 'alternative_hooks': 3 diverse high-curiosity hook options.

Output ONLY valid JSON matching this exact structure.
"""


def build_doctor_prompts(
    request: OptimizePostRequest,
    persona: Any | None = None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for Content Doctor optimization."""
    platform_rules = PLATFORM_GUIDELINES.get(request.platform, PLATFORM_GUIDELINES["telegram"])
    persona_section = _format_persona_clause(persona)

    sys_prompt = DOCTOR_SYSTEM_PROMPT.format(
        platform=request.platform.upper(),
        platform_rules=platform_rules,
        persona_section=persona_section,
    )

    user_instructions = [
        f"Draft Post to Review and Optimize:\n\"\"\"{request.draft_text}\"\"\"",
    ]
    if request.extra_instructions:
        user_instructions.append(f"Special Focus: {request.extra_instructions}")

    return sys_prompt, "\n".join(user_instructions)


# --- Repurposing Prompts ---

REPURPOSE_SYSTEM_PROMPT = """You are an omnichannel content repurposing master at ZeroIO Labs.
Given a core master idea or article, you adapt it into tailor-made, platform-native posts for: {target_platforms}.

{persona_section}

Rules for each requested platform:
{platform_rules}

Output ONLY valid JSON in this format:
{{
  "source_summary": "1-2 sentence distillation of core message",
  "posts": {{
    "<platform_code>": {{
      "headline": "catchy headline",
      "hook": "platform-tailored opening hook",
      "body": "complete body formatted with platform emojis, breaks, and style",
      "hashtags": ["#tag1", "#tag2"],
      "call_to_action": "closing CTA"
    }}
  }}
}}
"""


def build_repurpose_prompts(
    request: RepurposeRequest,
    persona: Any | None = None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for multi-platform repurposing."""
    platform_rules_joined = "\n\n".join(
        f"[{p.upper()}]:\n{PLATFORM_GUIDELINES.get(p, '')}" for p in request.target_platforms
    )
    persona_section = _format_persona_clause(persona)

    sys_prompt = REPURPOSE_SYSTEM_PROMPT.format(
        target_platforms=", ".join(request.target_platforms).upper(),
        persona_section=persona_section,
        platform_rules=platform_rules_joined,
    )

    user_prompt = f"Source Content to Repurpose:\n\"\"\"{request.source_text}\"\"\""
    if request.extra_instructions:
        user_prompt += f"\nAdditional Instructions: {request.extra_instructions}"

    return sys_prompt, user_prompt
