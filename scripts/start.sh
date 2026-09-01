#!/bin/sh
# نقطة إقلاع الإنتاج: تُطبَّق الترحيلات ثم يُشغَّل الخادم.
# المنفذ من PORT لأن منصات النشر تحقنه ولا تقبل ثابتًا.
set -e

echo "› تطبيق الترحيلات…"
alembic upgrade head

echo "› تشغيل الخادم على المنفذ ${PORT:-8000}"
exec uvicorn daif.web.app:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
