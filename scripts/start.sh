#!/bin/sh
# نقطة إقلاع الإنتاج: تُطبَّق الترحيلات ثم يُشغَّل الخادم.
# المنفذ من PORT لأن منصات النشر تحقنه ولا تقبل ثابتًا.

# لا نستعمل `set -e` على الترحيلات عمدًا. سقوطها كان يقتل الحاوية قبل أن
# يقوم الخادم، فيرى الناشر «Application failed to respond» بلا سبب ولا مكان
# يقرأ فيه الخطأ. الآن يُحفظ الخطأ ويقوم الخادم ليعرضه على كل مسار.
STARTUP_ERROR_FILE="${STARTUP_ERROR_FILE:-/tmp/daif-startup-error}"
export STARTUP_ERROR_FILE
rm -f "$STARTUP_ERROR_FILE"

# نكتب السجل لملف ثم نعرضه، ولا نمرّره بأنبوب: في sh حالة الخروج من
# `cmd | tee` هي حالة tee — وهي ناجحة دائمًا — فكان فرع الفشل لا يُنفَّذ أبدًا.
echo "› تطبيق الترحيلات…"
MIGRATE_LOG=/tmp/daif-migrate.log
if alembic upgrade head > "$MIGRATE_LOG" 2>&1; then
    cat "$MIGRATE_LOG"
else
    cat "$MIGRATE_LOG"
    echo "‼ فشلت الترحيلات. الخادم سيقوم ليعرض السبب."
    {
        echo "فشل تطبيق ترحيلات قاعدة البيانات."
        echo "—"
        tail -n 25 "$MIGRATE_LOG"
    } > "$STARTUP_ERROR_FILE"
fi

echo "› تشغيل الخادم على المنفذ ${PORT:-8000}"
exec uvicorn daif.web.app:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
