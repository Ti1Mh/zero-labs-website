"""CLI utility to create or promote a Super Admin (OWNER) account.

Usage:
    python create_admin.py
    python create_admin.py --phone 09123456789 --password MyStrongPassword --name "مدیر کل"
"""

import argparse
import asyncio
import inspect
import sys

from sqlalchemy import select

from app.auth.models import User
from app.auth.security import hash_password
from app.core.database import AsyncSessionLocal


def normalize_phone(phone: str) -> str:
    """Normalize phone to international E.164-like format."""
    clean = phone.strip()
    if clean.startswith("0098"):
        return "+98" + clean[4:]
    if clean.startswith("09"):
        return "+98" + clean[1:]
    if not clean.startswith("+"):
        return "+98" + clean
    return clean


async def create_or_promote_admin(phone: str, password: str, display_name: str) -> None:
    normalized_phone = normalize_phone(phone)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.phone_number == normalized_phone))
        user = result.scalar_one_or_none()
        if inspect.isawaitable(user):
            user = await user

        if user is not None:
            user.is_superuser = True
            user.is_verified = True
            user.is_active = True
            user.owner_user_id = None
            if password:
                user.password_hash = hash_password(password)
            if display_name:
                user.display_name = display_name
            await db.commit()
            print(f"\n✅ کاربر موجود با شماره {normalized_phone} با موفقیت به مدیر ارشد (Super Admin / OWNER) ارتقا یافت.")
        else:
            user = User(
                phone_number=normalized_phone,
                password_hash=hash_password(password),
                display_name=display_name,
                is_verified=True,
                is_active=True,
                is_superuser=True,
                owner_user_id=None,
            )
            db.add(user)
            await db.commit()
            print(f"\n✅ اکانت مدیر ارشد جدید (Super Admin / OWNER) با موفقیت ساخته شد.")

        print(f"📱 شماره موبایل ورود: {phone} (یا {normalized_phone})")
        print(f"🔑 رمز عبور: {password}")
        print(f"👤 نام نمایشی: {display_name}")
        print(f"🛡️ دسترسی: مدیر ارشد کل پلتفرم (دسترسی کامل به پنل بک‌آفیس /admin و تمام پلن‌ها و کاربران)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="ایجاد یا ارتقای کاربر مدیر ارشد سامانه (Super Admin / OWNER)")
    parser.add_argument("--phone", default="09120000000", help="شماره موبایل مدیر (پیش‌فرض: 09120000000)")
    parser.add_argument("--password", default="Admin@MezonFlow2026", help="رمز عبور مدیر (پیش‌فرض: Admin@MezonFlow2026)")
    parser.add_argument("--name", default="مدیر ارشد سامانه", help="نام نمایشی مدیر (پیش‌فرض: مدیر ارشد سامانه)")

    args = parser.parse_args()
    asyncio.run(create_or_promote_admin(phone=args.phone, password=args.password, display_name=args.name))


if __name__ == "__main__":
    main()
