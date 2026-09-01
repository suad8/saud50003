# ضيف — صورة الإنتاج
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Riyadh

WORKDIR /app

# الاعتماديات أولًا: طبقة تُخزَّن مؤقتًا ولا تُعاد بناؤها مع كل تغيير في الكود
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir \
      "anthropic>=1.2.0" "pydantic>=2.9" "fastapi>=0.115" "uvicorn[standard]>=0.30" \
      "httpx>=0.27" "sqlalchemy>=2.0" "jinja2>=3.1" "pyyaml>=6.0" \
      "python-multipart>=0.0.9" "itsdangerous>=2.2" "cryptography>=42" \
      "alembic>=1.13" "psycopg[binary]>=3.1"

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
