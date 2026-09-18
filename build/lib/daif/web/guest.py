"""صفحة النزيل — من مسح الملصق إلى المحادثة.

المسار كله عام: لا تسجيل دخول، ولا تطبيق، ولا رقم جوال يُكتب. النزيل يمسح
الملصق فيفتح المتصفح، يختار لغته، يثبت أنه في الغرفة مرة واحدة، ثم يتكلم.

المبدأ الحاكم هنا مختلف عن بقية الموقع: **كل طلب يُعامَل كأنه من غريب.**
الكوكي وحده ليس إذنًا — يُفحص مقابل إقامة مفتوحة في كل مرة، لأن المغادرة تقع
في منتصف المحادثة لا بين الجلسات.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import degraded, roomqr, stay as stays
from ..assistant import Assistant
from ..clock import now_riyadh
from ..db import get_session
from ..models import Guest, Message, Tenant
from ..ratelimit import GUEST_ENTER, GUEST_SEND, RateLimiter
from ..repository import get_or_create_guest, load_knowledge_base
from ..service import handle_inbound

# اسم الكوكي يحمل الغرفة، فجهاز واحد يقدر يفتح غرفتين (المطوّف وعائلته) بلا
# أن تدهس إحداهما الأخرى.
COOKIE = "daif_stay"

_limiter = RateLimiter()

LANGUAGES = [
    ("ar", "العربية"), ("en", "English"), ("ur", "اردو"), ("id", "Indonesia"),
    ("tr", "Türkçe"), ("bn", "বাংলা"), ("fa", "فارسی"), ("fr", "Français"),
    ("ms", "Melayu"), ("ha", "Hausa"),
]
_LANG_CODES = {code for code, _ in LANGUAGES}

# نصوص الواجهة. تُترجم عبر i18n لاحقًا؛ ما يظهر هنا هو الهيكل لا المحتوى.
PROOF_LABEL = {
    "stay_code": "اكتب رمز الإقامة المطبوع على ظرف بطاقة غرفتك",
    "phone_last4": "اكتب آخر أربعة أرقام من جوالك المسجّل عند الوصول",
    "desk_arm": "اطلب من الاستقبال تفعيل غرفتك، ثم امسح الكود مرة ثانية",
}
DENY_TEXT = {
    "no_active_stay": "ما فيه إقامة مفتوحة على هذي الغرفة. لو وصلت للتو، "
                      "الاستقبال بيفعّلها لك.",
    "device_limit": "عدد الأجهزة المسموح بها لهذي الغرفة اكتمل. الاستقبال "
                    "يقدر يضيف جهازك.",
    "proof_wrong": "الرمز ما ضبط. تأكد منه أو اسأل الاستقبال.",
    "window_closed": "انتهت مهلة التفعيل. اطلب من الاستقبال يفعّلها من جديد.",
    "not_configured": "هذي الغرفة محتاجة تفعيل من الاستقبال.",
    "device_revoked": "انتهت جلستك. لو ما زلت نازلًا، امسح الكود مرة ثانية.",
    "rate_limited": "محاولات كثيرة. انتظر شوي وجرّب مرة ثانية.",
}


@dataclass(frozen=True)
class Visit:
    """ما يعرفه الخادم عن هذا الطلب قبل أن يقرر شيئًا."""

    tenant: Tenant
    room: str
    signature: str

    @property
    def path(self) -> str:
        return f"/g/{self.tenant.slug}/{self.room}/{self.signature}"


def _cookie_name(room: str) -> str:
    safe = "".join(ch for ch in room if ch.isalnum())
    return f"{COOKIE}_{safe}"


def _client_key(request: Request, room: str) -> str:
    client = request.client.host if request.client else "?"
    return f"{client}:{room}"


def setup(templates) -> APIRouter:
    router = APIRouter(tags=["guest"])
    assistant = Assistant()

    def _visit(slug: str, room: str, sig: str, db: Session) -> Optional[Visit]:
        """يُرفض الرابط الملفّق قبل لمس قاعدة بيانات الإقامات.

        ترتيب مقصود: التوقيع أولًا وهو حساب محلي رخيص، ثم الفندق. فمن يجرّب
        أرقام غرف عشوائية لا يحمّل قاعدة البيانات شيئًا.
        """
        tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None or not roomqr.verify(slug, room, sig):
            return None
        return Visit(tenant=tenant, room=room, signature=sig)

    def _mode(tenant: Tenant) -> str:
        mode = getattr(tenant, "access_mode", "") or "stay_code"
        return mode if mode in stays.MODES else "stay_code"

    def _page(request: Request, name: str, ctx: dict) -> HTMLResponse:
        ctx.setdefault("languages", LANGUAGES)
        return templates.TemplateResponse(request, name, ctx)

    # ---------- المسح: اختيار اللغة ثم الإثبات ----------

    @router.get("/g/{slug}/{room}/{sig}", response_class=HTMLResponse)
    def landing(request: Request, slug: str, room: str, sig: str,
                db: Session = Depends(get_session)):
        visit = _visit(slug, room, sig, db)
        if visit is None:
            return _page(request, "guest_gone.html",
                         {"reason": "هذا الكود مو صحيح."}, )

        token = request.cookies.get(_cookie_name(room), "")
        access = stays.check(db, visit.tenant.id, room, token) if token else None
        if access is not None and access.granted:
            db.commit()
            return RedirectResponse(visit.path + "/chat", status_code=303)

        mode = _mode(visit.tenant)
        return _page(request, "guest_enter.html", {
            "visit": visit, "hotel": visit.tenant, "room": room,
            "mode": mode, "prompt": PROOF_LABEL[mode],
            "needs_input": mode != "desk_arm",
            "error": "",
        })

    @router.post("/g/{slug}/{room}/{sig}/enter")
    def enter(request: Request, slug: str, room: str, sig: str,
              language: str = Form("ar"), proof: str = Form(""),
              db: Session = Depends(get_session)):
        visit = _visit(slug, room, sig, db)
        if visit is None:
            return _page(request, "guest_gone.html", {"reason": "هذا الكود مو صحيح."})

        mode = _mode(visit.tenant)
        if language not in _LANG_CODES:
            language = "ar"

        def refuse(reason: str):
            return _page(request, "guest_enter.html", {
                "visit": visit, "hotel": visit.tenant, "room": room,
                "mode": mode, "prompt": PROOF_LABEL[mode],
                "needs_input": mode != "desk_arm",
                "error": DENY_TEXT.get(reason, "ما قدرنا نفتح الجلسة."),
                "language": language,
            })

        if not _limiter.hit(_client_key(request, room), GUEST_ENTER):
            return refuse("rate_limited")

        access = stays.enter(db, visit.tenant.id, room, mode=mode,
                             device_token=request.cookies.get(_cookie_name(room), ""),
                             proof=proof, language=language)
        if not access.granted:
            db.commit()
            return refuse(access.reason)

        db.commit()
        response = RedirectResponse(visit.path + "/chat", status_code=303)
        if access.token:
            # الكوكي هو المفتاح: على الجهاز، غير مقروء من جافاسكربت، ولا يُرسل
            # مع طلبات مواقع أخرى، ويموت مع الإقامة لا بعدها.
            response.set_cookie(
                _cookie_name(room), access.token,
                max_age=14 * 24 * 3600, httponly=True, samesite="lax",
                secure=request.url.scheme == "https", path=f"/g/{slug}/{room}",
            )
        response.set_cookie("daif_lang", language, max_age=14 * 24 * 3600,
                            samesite="lax", path=f"/g/{slug}/{room}")
        return response

    # ---------- المحادثة ----------

    def _guard(request: Request, visit: Visit, db: Session):
        """الفحص الذي يسبق كل شيء في هذه الصفحة."""
        token = request.cookies.get(_cookie_name(visit.room), "")
        if not token:
            return None, "no_active_stay"
        access = stays.check(db, visit.tenant.id, visit.room, token)
        return (access.stay, "") if access.granted else (None, access.reason)

    def _guest_of(db: Session, tenant_id: int, stay) -> Guest:
        """كل إقامة نزيلٌ واحد في السجلات.

        المعرّف مشتق من الإقامة لا من الغرفة: فمحادثة من غادر لا تُخلط بمحادثة
        من جاء بعده في نفس الغرفة، ولو كانا على نفس الجهاز.
        """
        guest = get_or_create_guest(db, tenant_id, f"web:{stay.id}")
        if not guest.room:
            guest.room = stay.room
        if stay.guest_name and not guest.name:
            guest.name = stay.guest_name
        return guest

    @router.get("/g/{slug}/{room}/{sig}/chat", response_class=HTMLResponse)
    def chat(request: Request, slug: str, room: str, sig: str,
             db: Session = Depends(get_session)):
        visit = _visit(slug, room, sig, db)
        if visit is None:
            return _page(request, "guest_gone.html", {"reason": "هذا الكود مو صحيح."})

        stay, reason = _guard(request, visit, db)
        if stay is None:
            db.commit()
            return _page(request, "guest_gone.html",
                         {"reason": DENY_TEXT.get(reason, ""), "visit": visit,
                          "retry": visit.path})

        guest = _guest_of(db, visit.tenant.id, stay)
        history = db.scalars(
            select(Message)
            .where(Message.tenant_id == visit.tenant.id, Message.guest_id == guest.id)
            .order_by(Message.created_at)
        ).all()
        db.commit()
        return _page(request, "guest_chat.html", {
            "visit": visit, "hotel": visit.tenant, "room": room, "stay": stay,
            "messages": history, "shortcuts": shortcuts_for(visit.tenant),
            "language": request.cookies.get("daif_lang", "ar"),
        })

    @router.post("/g/{slug}/{room}/{sig}/send")
    def send(request: Request, slug: str, room: str, sig: str,
             text: str = Form(""), shortcut: str = Form(""),
             db: Session = Depends(get_session)):
        visit = _visit(slug, room, sig, db)
        if visit is None:
            return JSONResponse({"error": "bad_code"}, status_code=404)

        stay, reason = _guard(request, visit, db)
        if stay is None:
            db.commit()
            return JSONResponse({"error": reason,
                                 "text": DENY_TEXT.get(reason, "")}, status_code=403)

        text = (text or "").strip()
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        if len(text) > 1000:
            text = text[:1000]
        if not _limiter.hit(_client_key(request, room), GUEST_SEND):
            return JSONResponse({"error": "rate_limited",
                                 "text": DENY_TEXT["rate_limited"]}, status_code=429)

        guest = _guest_of(db, visit.tenant.id, stay)
        lang = request.cookies.get("daif_lang", "") or guest.language
        guest.language = lang
        outcome = handle_inbound(db, visit.tenant, wa_id=guest.wa_id, text=text,
                                 assistant=assistant)
        if outcome is None:
            db.commit()
            return JSONResponse({"messages": []})

        reply = outcome.reply_text
        # النموذج سقط والسؤال من الأسئلة المعروفة: نقتبس الحقيقة الموثّقة
        # حرفيًا بدل أن نكدّس ثمانية أسئلة شائعة على الاستقبال.
        if outcome.result.degraded and shortcut:
            quote = degraded.quote_for(
                shortcut, load_knowledge_base(db, visit.tenant.id).facts, lang)
            if quote is not None:
                reply = quote.text
                outcome.outbound.text = reply
                outcome.outbound.intent = "degraded_quote"

        db.commit()
        return JSONResponse({"messages": [
            {"dir": "in", "text": text, "at": _hhmm(outcome.inbound.created_at)},
            {"dir": "out", "text": reply,
             "at": _hhmm(outcome.outbound.created_at),
             "ticket": bool(outcome.tickets)},
        ]})

    @router.get("/g/{slug}/{room}/{sig}/poll")
    def poll(request: Request, slug: str, room: str, sig: str, after: int = 0,
             db: Session = Depends(get_session)):
        """ردود الاستقبال تصل هنا.

        النزيل قد يغلق التبويب فلا يرى الرد. هذا الحد المعروف لقناة الويب،
        وعلاجه طبقة الإشعارات لا هذا المسار.
        """
        visit = _visit(slug, room, sig, db)
        if visit is None:
            return JSONResponse({"error": "bad_code"}, status_code=404)
        stay, reason = _guard(request, visit, db)
        if stay is None:
            db.commit()
            return JSONResponse({"error": reason,
                                 "text": DENY_TEXT.get(reason, "")}, status_code=403)

        guest = _guest_of(db, visit.tenant.id, stay)
        rows = db.scalars(
            select(Message)
            .where(Message.tenant_id == visit.tenant.id, Message.guest_id == guest.id,
                   Message.id > after, Message.direction == "out")
            .order_by(Message.id)
        ).all()
        db.commit()
        return JSONResponse({"messages": [
            {"id": m.id, "dir": "out", "text": m.text, "at": _hhmm(m.created_at),
             "by": m.sent_by or "", "staff": bool(m.sent_by)} for m in rows
        ]})

    return router


def _hhmm(moment) -> str:
    return moment.strftime("%H:%M") if moment else now_riyadh().strftime("%H:%M")


# أسئلة الضغطة الواحدة. الجواب لا يُكتب هنا — يُطلب من نفس المحرّك، فيمرّ على
# كل الحواجز ويتغيّر مع الموسم والوقت وحالة الحقيقة. زر ثابت بجواب مكتوب
# سلفًا هو بالضبط ما يجعل النظام يكذب بعد أول تعديل في قاعدة المعرفة.
DEFAULT_SHORTCUTS = [
    ("wifi", "🛜", "كلمة سر الواي فاي", "وش كلمة سر الواي فاي؟"),
    ("breakfast", "🍽️", "وقت الإفطار", "متى الإفطار ووين؟"),
    ("gate", "🕌", "أقرب باب للحرم", "وش أقرب باب للحرم؟"),
    ("towels", "🧺", "أبغى مناشف", "أبغى مناشف إضافية للغرفة"),
    ("clean", "🧹", "تنظيف الغرفة", "أبغى تنظيف الغرفة"),
    ("water", "💧", "ماء زمزم", "وين ألقى ماء زمزم؟"),
    ("laundry", "👕", "الغسيل", "كيف أرسل ملابسي للغسيل؟"),
    ("desk", "🛎️", "أكلّم الاستقبال", "أبغى أكلم الاستقبال"),
]


def shortcuts_for(tenant: Tenant) -> list[dict]:
    return [{"key": k, "icon": i, "label": lbl, "text": t}
            for k, i, lbl, t in DEFAULT_SHORTCUTS]
