"""حماية من تزوير الطلبات عبر المواقع (CSRF).

نمط الإرسال المزدوج: رمز عشوائي يوضع في كوكي، ويُحقن في كل نموذج، ويُقارن
عند الإرسال. موقع خارجي يستطيع دفع المتصفح لإرسال نموذج، لكنه لا يستطيع
قراءة الكوكي ليضع الرمز الصحيح في النموذج.

مسار الـwebhook مستثنى: لا يأتي من متصفح، وله تحقّق أقوى (توقيع Meta).
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request

COOKIE = "daif_csrf"
FIELD = "csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
EXEMPT_PREFIXES = ("/webhook/",)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_for(request: Request) -> str:
    """رمز هذه الجلسة، أو رمز جديد إن لم يوجد."""
    return request.cookies.get(COOKIE) or new_token()


def attach(request: Request, response, token: str) -> None:
    """يثبّت الرمز في كوكي إن لم يكن مثبّتًا أصلًا."""
    if request.cookies.get(COOKIE) != token:
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="lax", path="/", max_age=60 * 60 * 12
        )


async def guard(request: Request) -> None:
    """اعتمادية عامة تُطبَّق على كل المسارات وتفحص غير الآمنة منها."""
    if request.method in SAFE_METHODS:
        return
    path = request.url.path
    if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
        return

    expected = request.cookies.get(COOKIE)
    if not expected:
        raise HTTPException(status_code=403, detail="جلسة بلا رمز حماية — أعد تحميل الصفحة")

    form = await request.form()
    supplied = str(form.get(FIELD) or "")
    # المقارنة على البايتات: compare_digest يرمي استثناءً على النص غير ASCII،
    # وطلب خبيث برمز عربي كان يكفي لإحداث خطأ ٥٠٠.
    if not supplied or not hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="رمز حماية غير صالح")
