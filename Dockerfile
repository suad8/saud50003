# ضيف — صورة الإنتاج
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Riyadh

WORKDIR /app

# الاعتماديات أولًا: طبقة تُخزَّن مؤقتًا ولا تُعاد بناؤها مع كل تغيير في الكود.
# تُقرأ من pyproject لا من قائمة مكتوبة هنا: القائمتان المنفصلتان انحرفتا
# فعلًا، ونُشرت صورة بلا segno.
COPY pyproject.toml README.md ./
RUN mkdir -p daif && touch daif/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf daif

COPY daif ./daif
COPY prompts ./prompts
COPY data ./data
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

# التطبيق لا يعمل بصلاحيات الجذر
RUN useradd --create-home --uid 10001 daif \
    && mkdir -p /app/var \
    && chown -R daif:daif /app
USER daif

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=3).status==200 else 1)"

# الترحيلات ثم الخادم، والمنفذ من PORT
CMD ["./scripts/start.sh"]
