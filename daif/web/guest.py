"""صفحة النزيل — من الرابط الموحّد إلى المحادثة.

رابط واحد للفندق كله: ملصق واحد يُطبع بلا تغيير، ولا توقيع لكل غرفة، ولا
إعادة طباعة حين تتغيّر الأرقام. النزيل يفتحه، يختار لغته، يكتب جواله، فيُعرف
من الحجز — والغرفة تُستنتج من الإقامة لا من الرابط. ولهذا وحده يعمل نزيلٌ
نُقل إلى غرفة أخرى، وكان الملصق يكسر معه.

المبدأ الحاكم هنا مختلف عن بقية الموقع: **كل طلب يُعامَل كأنه من غريب.**
الكوكي وحده ليس إذنًا — يُفحص مقابل إقامة مفتوحة في كل مرة، لأن المغادرة تقع
في منتصف المحادثة لا بين الجلسات.

ورقم الغرفة لا يُعرض للنزيل. هو يعرف غرفته، والمستفيد الوحيد من عرضها من
كتب رقم جوال غيره — والرقم ليس سرًّا. فحُذف العرض، وشُدّد حدّ المحاولات.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import degraded, phones, stay as stays
from ..assistant import Assistant
from ..clock import now_riyadh
from ..db import get_session
from ..models import Guest, Message, Tenant
from ..ratelimit import GUEST_DOOR_IP, GUEST_DOOR_PHONE, GUEST_SEND, RateLimiter
from ..repository import get_or_create_guest, load_knowledge_base
from ..service import handle_inbound

# اسم الكوكي يحمل الفندق، فجهاز واحد يقدر يفتح فندقين (المطوّف يتنقّل) بلا
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
PHONE_LABEL = "اكتب رقم جوالك المسجّل عند الوصول"
ROOM_LABEL = "رقمك مسجّل على أكثر من غرفة — اكتب رقم غرفتك"

DENY_TEXT = {
    # رسالة واحدة لحالتي «الرقم غلط» و«ما فيه إقامة»: التفريق بينهما يقول
    # لمن يجرّب أرقامًا إن هذا الرقم نازلٌ هنا، وهي المعلومة التي لا نعطيها.
    "no_active_stay": "ما لقينا إقامة مفتوحة بهذا الرقم. تأكد من الرقم كما "
                      "سجّلته عند الوصول، أو اسأل الاستقبال.",
    "device_limit": "عدد الأجهزة المسموح بها لهذي الإقامة اكتمل. الاستقبال "
                    "يقدر يضيف جهازك.",
    "room_required": ROOM_LABEL,
    "room_wrong": "رقم الغرفة ما ضبط مع هذا الجوال.",
    "device_revoked": "انتهت جلستك. لو ما زلت نازلًا، افتح الرابط من جديد.",
    "rate_limited": "محاولات كثيرة. انتظر شوي وجرّب مرة ثانية.",
    "bad_phone": "الرقم ما بان صحيح. اكتبه بالأرقام، مثال: 0501234567",
}


@dataclass(frozen=True)
class Visit:
    """ما يعرفه الخادم عن هذا الطلب قبل أن يقرر شيئًا."""

    tenant: Tenant

    @property
    def path(self) -> str:
        return f"/h/{self.tenant.slug}"


def _cookie_name(slug: str) -> str:
    safe = "".join(ch for ch in slug if ch.isalnum())
    return f"{COOKIE}_{safe}"


def _client_key(request: Request, scope: str) -> str:
    client = request.client.host if request.client else "?"
    return f"{client}:{scope}"


def setup(templates) -> APIRouter:
    router = APIRouter(tags=["guest"])
    assistant = Assistant()

    def _visit(slug: str, db: Session) -> Optional[Visit]:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
        return Visit(tenant=tenant) if tenant is not None else None

    def _page(request: Request, name: str, ctx: dict) -> HTMLResponse:
        ctx.setdefault("languages", LANGUAGES)
        return templates.TemplateResponse(request, name, ctx)

    # ---------- المسح: اختيار اللغة ثم الإثبات ----------

    @router.get("/h/{slug}", response_class=HTMLResponse)
    def door(request: Request, slug: str, db: Session = Depends(get_session)):
        visit = _visit(slug, db)
        if visit is None:
            return _page(request, "guest_gone.html", {"reason": "هذا الرابط مو صحيح."})

        token = request.cookies.get(_cookie_name(slug), "")
        if token and stays.check_device(db, visit.tenant.id, token).granted:
            db.commit()
            return RedirectResponse(visit.path + "/chat", status_code=303)
        db.commit()
        return _page(request, "guest_enter.html", {
            "visit": visit, "hotel": visit.tenant, "prompt": PHONE_LABEL,
            "error": "", "needs_room": False, "phone": "",
        })

    @router.post("/h/{slug}/enter")
    def enter(request: Request, slug: str, phone: str = Form(""),
              room: str = Form(""), language: str = Form("ar"),
              db: Session = Depends(get_session)):
        visit = _visit(slug, db)
        if visit is None:
            return _page(request, "guest_gone.html", {"reason": "هذا الرابط مو صحيح."})

        if language not in _LANG_CODES:
            language = "ar"

        def refuse(reason: str, *, needs_room: bool = False):
            return _page(request, "guest_enter.html", {
                "visit": visit, "hotel": visit.tenant,
                "prompt": ROOM_LABEL if needs_room else PHONE_LABEL,
                "error": DENY_TEXT.get(reason, "ما قدرنا نفتح الجلسة."),
                "needs_room": needs_room, "phone": phone, "language": language,
            })

        # حدّان معًا: على المصدر وعلى الرقم المطلوب. الأول يوقف من يجرّب
        # أرقامًا كثيرة، والثاني يوقف من يجرّب رقمًا واحدًا من مصادر كثيرة.
        normalized = phones.normalize(phone)
        if not _limiter.hit(_client_key(request, f"door:{slug}"), GUEST_DOOR_IP):
            return refuse("rate_limited")
        if normalized and not _limiter.hit(f"phone:{slug}:{normalized}", GUEST_DOOR_PHONE):
            return refuse("rate_limited")
        if not normalized:
            return refuse("bad_phone")

        access = stays.enter_by_phone(
            db, visit.tenant.id, phone, room=room, language=language,
            device_token=request.cookies.get(_cookie_name(slug), ""))
        if not access.granted:
            db.commit()
            return refuse(access.reason, needs_room=access.challenge == "room")

        db.commit()
        response = RedirectResponse(visit.path + "/chat", status_code=303)
        if access.token:
            # الكوكي هو المفتاح: على الجهاز، غير مقروء من جافاسكربت، ولا يُرسل
            # مع طلبات مواقع أخرى، ويموت مع الإقامة لا بعدها.
            response.set_cookie(
                _cookie_name(slug), access.token, httponly=True, samesite="lax",
                secure=request.url.scheme == "https", path=f"/h/{slug}",
                max_age=int(stays.CHECKOUT_GRACE.total_seconds()) + 7 * 86400)
        response.set_cookie("daif_lang", language, max_age=7 * 86400,
                            samesite="lax", path=f"/h/{slug}")
        return response

    def _guard(request: Request, visit: Visit, db: Session):
        """الفحص الذي يسبق كل شيء في هذه الصفحة."""
        token = request.cookies.get(_cookie_name(visit.tenant.slug), "")
        if not token:
            return None, "device_revoked"
        access = stays.check_device(db, visit.tenant.id, token)
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

    @router.get("/h/{slug}/chat", response_class=HTMLResponse)
    def chat(request: Request, slug: str, db: Session = Depends(get_session)):
        visit = _visit(slug, db)
        if visit is None:
            return _page(request, "guest_gone.html", {"reason": "هذا الرابط مو صحيح."})

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
        # لا "room" في السياق: رقم الغرفة لا يُعرض للنزيل. هو يعرف غرفته،
        # والمستفيد الوحيد من عرضها من كتب رقم جوال غيره.
        return _page(request, "guest_chat.html", {
            "visit": visit, "hotel": visit.tenant, "stay": stay,
            "messages": history, "shortcuts": shortcuts_for(visit.tenant),
            "language": request.cookies.get("daif_lang", "ar"),
        })

    @router.post("/h/{slug}/send")
    def send(request: Request, slug: str, text: str = Form(""),
             shortcut: str = Form(""), db: Session = Depends(get_session)):
        visit = _visit(slug, db)
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
        if not _limiter.hit(_client_key(request, f"send:{slug}"), GUEST_SEND):
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

    @router.get("/h/{slug}/poll")
    def poll(request: Request, slug: str, after: int = 0,
             db: Session = Depends(get_session)):
        """ردود الاستقبال تصل هنا.

        النزيل قد يغلق التبويب فلا يرى الرد. هذا الحد المعروف لقناة الويب،
        وعلاجه طبقة الإشعارات لا هذا المسار.
        """
        visit = _visit(slug, db)
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
