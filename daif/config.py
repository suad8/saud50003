"""إعدادات المنصة، تُقرأ من متغيرات البيئة."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# أسماء المتغيّر التي تحقنها منصات النشر لعنوان Postgres. Railway وحدها
# تستعمل ثلاثة منها حسب طريقة الربط، وHeroku وRender وFly لكلٍّ اسمه. نقرأها
# كلها بدل أن يُرفض إقلاع سليم لأن الناشر نسخ الاسم الآخر.
PG_URL_ENV_NAMES = (
    "DAIF_DATABASE_URL",
    "DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "DATABASE_PUBLIC_URL",
    "POSTGRES_URL",
    "POSTGRESQL_URL",
)


def _pg_url_from_parts() -> str:
    """يركّب العنوان من متغيّرات libpq المنفصلة إن وُجدت بلا عنوان كامل."""
    host = _env("PGHOST", "")
    name = _env("PGDATABASE", "")
    user = _env("PGUSER", "")
    if not (host and name and user):
        return ""
    from urllib.parse import quote

    password = _env("PGPASSWORD", "")
    auth = quote(user, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    port = _env("PGPORT", "5432")
    return f"postgresql+psycopg://{auth}@{host}:{port}/{name}"


def discovered_database_url() -> str:
    """العنوان الذي وجدناه في البيئة، أو فراغ إن لم يُضبط أي اسم.

    مفصول عن `_database_url` كي يستعمله فحص ما قبل الإقلاع بنفس المنطق: أسوأ
    ما قد يحصل أن يوقف الفحص إقلاعًا كانت قاعدة البيانات فيه موجودة فعلًا.
    يرجع ما وُجد كما هو — حتى لو كان SQLite صريحًا؛ الحكم على نوعه ليس هنا.
    """
    for name in PG_URL_ENV_NAMES:
        value = _env(name, "")
        if value:
            return _normalise_pg(value)
    return _pg_url_from_parts()


def _database_url() -> str:
    """عنوان قاعدة البيانات، مع تكيّف مع منصات النشر.

    Railway وHeroku وأمثالهما يحقنون العنوان عند ربط قاعدة بيانات، وبصيغة
    `postgres://` أو `postgresql://` التي لا يفهمها SQLAlchemy 2 بلا اسم
    المشغّل. نحوّلها هنا بدل أن يكتشف المشغّل الخطأ عند أول إقلاع.
    """
    return discovered_database_url() or "sqlite:///var/daif.db"


def _normalise_pg(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def server_port() -> int:
    """المنفذ. منصات النشر تحقن PORT ويجب الاستماع عليه هو لا على ثابت."""
    return _env_int("PORT", 8000)


@dataclass(frozen=True)
class Settings:
    """إعدادات عامة. القيم الحسّاسة لا تُخزَّن هنا بل تُقرأ من البيئة عند الحاجة."""

    # --- النموذج ---
    model: str = field(default_factory=lambda: _env("DAIF_MODEL", "claude-opus-5"))
    # مهمة تصنيف واستخراج بقواعد صارمة، والزمن مهم على واتساب — "medium" توازن معقول.
    effort: str = field(default_factory=lambda: _env("DAIF_EFFORT", "medium"))
    # المخرجات JSON صغير، لكن تفكير النموذج يُحتسب ضمن السقف — نترك هامشًا.
    max_tokens: int = field(default_factory=lambda: _env_int("DAIF_MAX_TOKENS", 4000))
    request_timeout: float = field(default_factory=lambda: _env_float("DAIF_TIMEOUT", 45.0))

    # --- الحواجز ---
    # دون هذه الثقة يُحوَّل الطلب لموظف بشري مهما كان الجواب.
    confidence_threshold: float = field(
        default_factory=lambda: _env_float("DAIF_CONFIDENCE_THRESHOLD", 0.7)
    )
    max_sentences: int = field(default_factory=lambda: _env_int("DAIF_MAX_SENTENCES", 3))

    # --- التخزين ---
    database_url: str = field(default_factory=lambda: _database_url())

    # --- واتساب ---
    wa_api_version: str = field(default_factory=lambda: _env("WHATSAPP_API_VERSION", "v21.0"))
    wa_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN", ""))
    wa_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET", ""))

    # --- اللوحة ---
    dashboard_secret: str = field(default_factory=lambda: _env("DAIF_DASHBOARD_SECRET", ""))
    default_locale: str = field(default_factory=lambda: _env("DAIF_DEFAULT_LOCALE", "ar"))


def get_settings() -> Settings:
    """تُقرأ الإعدادات عند كل نداء ليسهل تغييرها في الاختبارات."""
    return Settings()
