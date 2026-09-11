"""Content Safety and Moderation Guardrail for social media publication."""

import re

# Blocklist of high-risk / hazardous phrases, scams, and profanities
PROHIBITED_PATTERNS = [
    r"شماره\s*کارت\s*و\s*رمز",
    r"رمز\s*دوم\s*پویا",
    r"واریز\s*وجه\s*اجباری",
    r"برنده\s*شدید.*واریز\s*کنید",
    r"سایت\s*قمار\s*و\s*شرط‌بندی",
    r"کازینو\s*آنلاین",
    r"خرید\s*فیلترشکن\s*قانونی",
    r"خرید\s*سلاح",
    r"مواد\s*مخدر",
    r"fuck\b",
    r"shit\b",
    r"bitch\b",
    r"scam\b",
]

COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in PROHIBITED_PATTERNS]


def check_content_safety(text: str) -> tuple[bool, str | None]:
    """Scan content for safety violations before generation or publishing.

    Returns:
        (is_safe, violation_reason)
    """
    if not text:
        return True, None

    for pattern in COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            matched_term = match.group(0)
            return False, f"محتوا حاوی عبارت غیرمجاز یا پرخطر است: '{matched_term}'"
    return True, None


CHILDREN_THEME_PATTERNS = [
    r"کودک(ان)?\b",
    r"خردسال(ان)?\b",
    r"مهد\s*کودک",
    r"کارتون\s*کودک",
    r"شعر\s*کودکانه",
    r"اسباب\s*بازی\s*خردسال",
    r"\bnursery\s*rhyme\b",
    r"\bfor\s*kids\b",
    r"\btoddler\b",
    r"\bchildren\s*cartoon\b",
]

COMPILED_CHILDREN_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in CHILDREN_THEME_PATTERNS
]


def check_coppa_consistency(text: str, made_for_kids: bool) -> tuple[bool, str | None]:
    """Check whether content targeting children is consistent with COPPA declaration.

    Returns:
        (is_consistent, advisory_warning)
    """
    if not text:
        return True, None

    is_children_themed = any(p.search(text) for p in COMPILED_CHILDREN_PATTERNS)
    if is_children_themed and not made_for_kids:
        return (
            False,
            "هشدار رگولاتوری گوگل: محتوا دارای المان‌های مخصوص کودکان و خردسالان است؛ لطفاً تنظیم 'madeForKids' را بررسی نمایید تا از جریمه یا تعلیق کانال جلوگیری شود.",
        )

    return True, None
