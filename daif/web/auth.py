"""جلسات لوحة التحكم — كوكي موقّع، بلا تخزين جلسات على الخادم."""

from __future__ import annotations

import secrets

from itsdangerous import BadSignature, URLSafeSerializer

from ..config import get_settings

COOKIE_NAME = "daif_session"
_MAX_AGE = 60 * 60 * 12  # اثنتا عشرة ساعة — طول وردية


def _serializer() -> URLSafeSerializer:
    secret = get_settings().dashboard_secret
    if not secret:
        # بيئة تطوير بلا سرّ: مفتاح عشوائي لكل تشغيل، فتنتهي الجلسات عند إعادة التشغيل.
        secret = _dev_secret()
    return URLSafeSerializer(secret, salt="daif-dashboard")


_DEV_SECRET: str | None = None


def _dev_secret() -> str:
    global _DEV_SECRET
    if _DEV_SECRET is None:
        _DEV_SECRET = secrets.token_urlsafe(32)
    return _DEV_SECRET


def issue(staff_id: int, tenant_id: int) -> str:
    return _serializer().dumps({"u": staff_id, "t": tenant_id})


def read(raw: str | None) -> tuple[int, int] | None:
    """يعيد (staff_id, tenant_id) أو None إن كان التوقيع غير صالح."""
    if not raw:
        return None
    try:
        data = _serializer().loads(raw)
    except BadSignature:
        return None
    try:
        return int(data["u"]), int(data["t"])
    except (KeyError, TypeError, ValueError):
        return None


def cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "max_age": _MAX_AGE,
        "path": "/",
    }
