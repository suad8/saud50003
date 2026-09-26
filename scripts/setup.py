#!/usr/bin/env python3
"""يولّد أسرار الإنتاج ويطبع ما يُلصق في إعدادات الاستضافة.

سبب وجوده: خطوة الرفع الأولى كانت تتطلّب توليد سرّين يدويًا، وقراءة أسمائهما
من وثيقة، وعدم الخلط بينهما. وكل واحدة من الثلاث مكان يُخطأ فيه، وأثرها لا
يظهر إلا بعد النشر.

    python scripts/setup.py                 # أسرار جديدة
    python scripts/setup.py --whatsapp      # مع حقول واتساب
"""

from __future__ import annotations

import argparse
import secrets


def key() -> str:
    return secrets.token_urlsafe(48)


def main() -> int:
    ap = argparse.ArgumentParser(description="تجهيز متغيّرات الإنتاج")
    ap.add_argument("--email", default="you@example.com", help="بريد أول مشغّل للمنصة")
    ap.add_argument("--whatsapp", action="store_true", help="أضف حقول واتساب")
    args = ap.parse_args()

    admin_pw = secrets.token_urlsafe(18)          # أطول بكثير من حدّ الاثني عشر
    lines = [
        "DAIF_ENV=production",
        f"DAIF_SECRET_KEY={key()}",
        f"DAIF_DASHBOARD_SECRET={key()}",
        f"DAIF_BOOTSTRAP_ADMIN_EMAIL={args.email}",
        f"DAIF_BOOTSTRAP_ADMIN_PASSWORD={admin_pw}",
        "ANTHROPIC_API_KEY=ضع-مفتاحك-هنا",
    ]
    if args.whatsapp:
        lines += [f"WHATSAPP_VERIFY_TOKEN={secrets.token_urlsafe(24)}",
                  "WHATSAPP_APP_SECRET=من-لوحة-Meta",
                  "WHATSAPP_PHONE_NUMBER_ID=من-لوحة-Meta",
                  "WHATSAPP_ACCESS_TOKEN=من-لوحة-Meta"]

    print("\n".join(lines))
    print()
    print("— انسخ ما فوق إلى Variables في الاستضافة.", flush=True)
    print("— DATABASE_URL تحقنه المنصة نفسها لما تربط Postgres. لا تضبطه يدويًا.")
    print(f"— كلمة مرور أول دخول: {admin_pw}")
    print("  احفظها الآن. بعد أول دخول احذف متغيّري BOOTSTRAP من الإعدادات.")
    print("— DAIF_SECRET_KEY يوقّع أكواد غرفك: تغييره يُبطل كل الملصقات المطبوعة.")
    print("  احتفظ بنسخة منه في مكان آمن خارج الاستضافة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
