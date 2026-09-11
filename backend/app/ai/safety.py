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
            return False, f"محتوا حاوی عبارت غیرمجاز است: '{matched_term}'"

    return True, None
