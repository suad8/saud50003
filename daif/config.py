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
    database_url: str = field(
        default_factory=lambda: _env("DAIF_DATABASE_URL", "sqlite:///var/daif.db")
    )

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
