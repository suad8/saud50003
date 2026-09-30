"""إعدادات المنصة، تُقرأ من متغيرات البيئة."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


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


def usable_url(value: str) -> bool:
    """هل يقرأ SQLAlchemy هذا عنوانًا؟ نسأله هو ولا نخمّن بتعبير نمطي."""
    if not value or "://" not in value:
        return False
    try:
        from sqlalchemy.engine.url import make_url

        make_url(value)
    except Exception:
        return False
    return True


_URL_IN_TEXT = re.compile(r"postgres(?:ql)?://[^\s\"'<>,;]+")

# أسماء أجزاء الاتصال كما تحقنها المنصّات، مرتّبةً بالأفضلية داخل كل دور.
_PART_NAMES = {
    "host": ("PGHOST", "POSTGRES_HOST", "DB_HOST", "RAILWAY_PRIVATE_DOMAIN"),
    "name": ("PGDATABASE", "POSTGRES_DB", "DB_NAME"),
    "user": ("PGUSER", "POSTGRES_USER", "DB_USER"),
    "password": ("PGPASSWORD", "POSTGRES_PASSWORD", "DB_PASSWORD"),
    "port": ("PGPORT", "POSTGRES_PORT", "DB_PORT"),
}


def _assemble(lookup) -> str:
    """يركّب العنوان من أجزاء منفصلة. `lookup(name)` يرجع القيمة أو فراغًا."""
    from urllib.parse import quote

    def first(role: str) -> str:
        for name in _PART_NAMES[role]:
            value = lookup(name)
            if value:
                return value
        return ""

    host, name, user = first("host"), first("name"), first("user")
    if not (host and name and user):
        return ""
    auth = quote(user, safe="")
    password = first("password")
    if password:
        auth += ":" + quote(password, safe="")
    return f"postgresql+psycopg://{auth}@{host}:{first('port') or '5432'}/{name}"


def _pg_url_from_parts() -> str:
    """يركّب العنوان من متغيّرات منفصلة في البيئة إن وُجدت بلا عنوان كامل."""
    return _assemble(lambda name: _env(name, ""))


def recover_from_blob(raw: str) -> str:
    """ينتشل عنوانًا من قيمة لُصقت فيها كتلة متغيّرات كاملة.

    غلط متكرّر ومكلف: تُنسخ قائمة متغيّرات خدمة قاعدة البيانات كلها وتُلصق
    في خانة قيمة واحدة. القيمة حينها ليست عنوانًا، والنتيجة أثر تتبّع من
    SQLAlchemy وجولة نشر ضائعة في كل محاولة.

    ولأن الانتشال لا يجري إلا على قيمة **معطوبة أصلًا**، فأسوأ ما يفعله أن
    يفشل كما كانت تفشل — ولا يمسّ إعدادًا سليمًا أبدًا.
    """
    if not raw:
        return ""

    match = _URL_IN_TEXT.search(raw)
    if match and usable_url(match.group(0)):
        return _normalise_pg(match.group(0))

    pairs: dict[str, str] = {}
    for chunk in re.split(r"[\s]+", raw.strip()):
        key, sep, value = chunk.partition("=")
        if sep and key:
            pairs[key.strip()] = value.strip().strip("\"'")

    for name in PG_URL_ENV_NAMES:
        candidate = _normalise_pg(pairs.get(name, ""))
        if usable_url(candidate):
            return candidate

    assembled = _assemble(lambda name: pairs.get(name, ""))
    return assembled if usable_url(assembled) else ""


@dataclass(frozen=True)
class Discovery:
    """نتيجة البحث عن قاعدة البيانات، بما فيها ما رُفض ولماذا."""

    url: str = ""
    source: str = ""
    recovered: bool = False
    rejected: tuple[tuple[str, str], ...] = ()


def discover() -> Discovery:
    """يبحث عن عنوان صالح، وينتشل من قيمة معطوبة، ويبلّغ عمّا رفضه.

    القاعدة: قيمة لا تصلح عنوانًا لا تُستعمل. كانت تُستعمل، فتحجب البديل
    المحلّي وتُسقط الإقلاع حتى بعد إلغاء وضع الإنتاج — عطلٌ بلا مخرج.
    """
    rejected: list[tuple[str, str]] = []
    for name in PG_URL_ENV_NAMES:
        raw = _env(name, "")
        if not raw:
            continue
        direct = _normalise_pg(raw)
        if usable_url(direct) or direct.startswith("sqlite"):
            return Discovery(url=direct, source=name)
        salvaged = recover_from_blob(raw)
        if salvaged:
            logger.warning(
                "قيمة %s ليست عنوانًا، لكن انتُشل منها عنوان صالح. "
                "أصلحها في لوحة الاستضافة — الانتشال ليس بديلًا عن إعداد سليم.",
                name)
            return Discovery(url=salvaged, source=name, recovered=True)
        rejected.append((name, raw))
        logger.error("قيمة %s ليست عنوانًا ولا يمكن انتشال عنوان منها — تُتجاهل.", name)

    assembled = _pg_url_from_parts()
    if assembled:
        return Discovery(url=assembled, source="PGHOST/PGUSER/…")
    return Discovery(rejected=tuple(rejected))


def discovered_database_url() -> str:
    """العنوان الصالح الذي وُجد، أو فراغ. لا يرجع قيمة معطوبة أبدًا."""
    return discover().url


def _database_url() -> str:
    """عنوان قاعدة البيانات. يرجع للقاعدة المحلّية حين لا يوجد عنوان صالح.

    وهذا الرجوع هو المخرج: قيمةٌ معطوبة كانت تُستعمل كما هي فتحجبه، فيسقط
    الإقلاع حتى بعد إلغاء وضع الإنتاج — عطلٌ لا مهرب منه إلا بإصلاح اللوحة.
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
