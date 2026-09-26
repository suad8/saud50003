"""جلسات لوحة التحكم — كوكي موقّع، بلا تخزين جلسات على الخادم."""

from __future__ import annotations

import secrets

from itsdangerous import BadSignature, URLSafeSerializer

from ..config import get_settings

COOKIE_NAME = "daif_session"
_MAX_AGE = 60 * 60 * 12  # اثنتا عشرة ساعة — طول وردية


_DEV_SECRET: str | None = None


def signing_secret() -> str:
    """سرّ توقيع الجلسات.

    الترتيب: متغيّر البيئة أولًا وهو الأقوى، ثم سرّ مولَّد محفوظ في قاعدة
    البيانات. الثاني يعمل بلا إعداد ويصمد أمام إعادة التشغيل — وهو ما يهمّ
    الموظف الذي كان يجد نفسه خارج اللوحة بعد كل نشر.
    """
    secret = get_settings().dashboard_secret
    if secret:
        return secret
    # مولَّد محفوظ في قاعدة البيانات: يبقى عبر إعادة التشغيل، بخلاف المولَّد
    # لعمر العملية الذي كان يسقط جلسات كل الموظفين بلا سبب ظاهر.
    from ..secrets_store import get as stored
    return stored("dashboard")


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(signing_secret(), salt="daif-dashboard")


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


def cookie_kwargs(secure: bool = False) -> dict:
    """خصائص كوكي الجلسة.

    `secure` يمنع المتصفح من إرسال الكوكي على http. يُفعَّل تلقائيًا حين
    يكون الطلب على https — فلا يتعطّل التطوير المحلي ولا يبقى الإنتاج مكشوفًا.
    """
    return {
        "httponly": True,
        "samesite": "lax",
        "max_age": _MAX_AGE,
        "path": "/",
        "secure": secure,
    }
