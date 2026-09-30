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

from .. import local_answer, phones, stay as stays
from ..assistant import Assistant
from ..clock import now_riyadh
from ..db import get_session
from ..models import Guest, HandoffRecord, Message, Stay, Tenant, Ticket
from ..ratelimit import GUEST_DOOR_IP, GUEST_DOOR_PHONE, GUEST_SEND, RateLimiter
from ..repository import get_or_create_guest, load_knowledge_base

# اسم الكوكي يحمل الفندق، فجهاز واحد يقدر يفتح فندقين (المطوّف يتنقّل) بلا
# أن تدهس إحداهما الأخرى.
COOKIE = "daif_stay"

_limiter = RateLimiter()

# لغتان لا عشر. عشرة أزرار تملأ الشاشة قبل أن يصل النزيل إلى خانة جواله،
# وحقائق الفندق مكتوبة بالعربية فما عداها يحتاج ترجمة لا نعد بها.
LANGUAGES = [("ar", "العربية"), ("en", "English")]
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

# ما يُقال للنزيل حين يكتب سؤالًا بيده. لا وعد بوقت — الوعد الذي يُخلَف
# أسوأ من لا وعد، والاستقبال قد يكون مشغولًا.
TO_DESK = "وصل سؤالك للاستقبال، وبيردّون عليك هنا."


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

    def _shortcut_answer(db: Session, tenant: Tenant, shortcut: str,
                         language: str) -> str:
        """جواب زرّ الاختصار من قاعدة المعرفة. فراغٌ إن لم تغطّه حقيقة ظاهرة.

        والفراغ يعني الذهاب للموظف — فحقيقة حجبها الموسم أو انتهت صلاحيتها
        لا يُخترع لها بديل.
        """
        question = SHORTCUT_QUESTION.get(shortcut, "")
        if not question:
            return ""
        kb = load_knowledge_base(db, tenant.id)
        found = local_answer.answer(question, kb.active(now_riyadh(), tenant.season),
                                    language="ar")
        return found.text if found else ""

    def _demo_phone(db: Session, tenant: Tenant) -> str:
        """جوال إقامة مفتوحة، في وضع العرض وحده.

        الباب يسأل عن رقم مسجَّل، ومن يجرّب النظام لا يملك واحدًا — فيكتب
        رقمه هو ويُردّ. الشاشة تُقرأ عطلًا وهي تعمل كما يجب. وصفحة الدخول
        تُعلن بياناتها لنفس السبب، فهذا اتّساق لا استثناء.
        """
        from .. import demo as demo_mod

        if demo_mod.banner(db) is None:
            return ""
        stay = db.scalar(
            select(Stay).where(Stay.tenant_id == tenant.id, Stay.status == "open",
                               Stay.phone_norm != "").order_by(Stay.id)
        )
        return getattr(stay, "phone_norm", "") or ""

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
        demo_phone = _demo_phone(db, visit.tenant)
        db.commit()
        return _page(request, "guest_enter.html", {
            "visit": visit, "hotel": visit.tenant, "prompt": PHONE_LABEL,
            "error": "", "needs_room": False, "phone": "",
            "demo_phone": demo_phone,
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
                "demo_phone": _demo_phone(db, visit.tenant),
            })

        # حدّان معًا: على المصدر وعلى الرقم المطلوب. الأول يوقف من يجرّب
        # أرقامًا كثيرة، والثاني يوقف من يجرّب رقمًا واحدًا من مصادر كثيرة.
        #
        # ولا يُحتسب إلا الفشل. كان كل إدخال يستهلك محاولة ولو كان صحيحًا،
        # فنزيلٌ فتح الرابط خمس مرات — وهو يفعل: يقفل التبويب ويعود — يجد
        # الباب مقفلًا في وجهه. الحدّ لمنع التخمين، والداخل بحقّه لا يخمّن.
        normalized = phones.normalize(phone)
        by_ip = _client_key(request, f"door:{slug}")
        by_phone = f"phone:{slug}:{normalized}" if normalized else ""

        def spend(reason: str, *, needs_room: bool = False):
            """يسجّل محاولة فاشلة ثم يعتذر."""
            _limiter.hit(by_ip, GUEST_DOOR_IP)
            if by_phone:
                _limiter.hit(by_phone, GUEST_DOOR_PHONE)
            return refuse(reason, needs_room=needs_room)

        if not _limiter.remaining(by_ip, GUEST_DOOR_IP):
            return refuse("rate_limited")
        if by_phone and not _limiter.remaining(by_phone, GUEST_DOOR_PHONE):
            return refuse("rate_limited")
        if not normalized:
            return spend("bad_phone")

        access = stays.enter_by_phone(
            db, visit.tenant.id, phone, room=room, language=language,
            device_token=request.cookies.get(_cookie_name(slug), ""))
        if not access.granted:
            db.commit()
            # سؤال الغرفة ليس فشلًا: رقمه صحيح وعلى عدّة غرف، فلا يُعاقَب عليه.
            if access.challenge == "room":
                return refuse(access.reason, needs_room=True)
            return spend(access.reason)

        # دخل بحقّه: نمحو ما سُجِّل عليه، فلا تلاحقه محاولاته المتعثّرة.
        if by_phone:
            _limiter.reset(by_phone)
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
        # من كتب للتو يصعد أول قائمة المحادثات. بلا هذا بقي في مكانه، وفتحت
        # الشاشة محادثة نزيلٍ آخر — فردّ الموظف على غير من سأله.
        guest.last_seen_at = now_riyadh()

        inbound = Message(tenant_id=visit.tenant.id, guest_id=guest.id,
                          direction="in", text=text, language=lang)
        db.add(inbound)
        db.flush()

        # الاختصار جوابه من قاعدة المعرفة مباشرة. أما ما يكتبه النزيل بيده
        # فيذهب للموظف كما هو — لا يُخمَّن له جواب ولا يُعاد صياغته. السؤال
        # المكتوب قد يكون شكوى أو طلبًا أو ظرفًا خاصًّا، وإنسانٌ يقرؤه أصدق
        # من أي مطابقة، والفندق يبقى صاحب كل كلمة تصل نزيله.
        answer = _shortcut_answer(db, visit.tenant, shortcut, lang) if shortcut else ""

        if answer:
            outbound = Message(tenant_id=visit.tenant.id, guest_id=guest.id,
                               direction="out", text=answer, language=lang,
                               intent="inquiry", in_scope=True, confidence=1.0)
            db.add(outbound)
            db.flush()
            db.commit()
            return JSONResponse({"messages": [
                {"dir": "in", "text": text, "at": _hhmm(inbound.created_at)},
                {"dir": "out", "text": answer, "at": _hhmm(outbound.created_at)},
            ]})

        outbound = Message(tenant_id=visit.tenant.id, guest_id=guest.id,
                           direction="out", text=TO_DESK, language=lang,
                           intent="handoff", in_scope=False, confidence=1.0)
        db.add(outbound)
        db.flush()
        db.add(HandoffRecord(
            tenant_id=visit.tenant.id, guest_id=guest.id, message_id=outbound.id,
            reason="guest_question", to="front_desk", guest_text=text,
            note=f"سؤال مكتوب من غرفة {stay.room}"))
        db.add(Ticket(
            tenant_id=visit.tenant.id, guest_id=guest.id, message_id=outbound.id,
            type="استفسار", room=stay.room, detail=text, urgency="normal"))
        db.commit()
        return JSONResponse({"messages": [
            {"dir": "in", "text": text, "at": _hhmm(inbound.created_at)},
            {"dir": "out", "text": TO_DESK, "at": _hhmm(outbound.created_at),
             "ticket": True},
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


# أسئلة الضغطة الواحدة. الجواب لا يُكتب هنا — يُطلب من قاعدة المعرفة عند كل
# ضغطة، فيتغيّر مع الموسم والوقت وصلاحية الحقيقة. زر ثابت بجواب مكتوب سلفًا
# هو بالضبط ما يجعل النظام يكذب بعد أول تعديل في قاعدة المعرفة.
#
# ومنها ما هو طلب خدمة لا سؤال — مناشف، تنظيف، مكالمة الاستقبال. هذي لا
# يُقتبس لها جواب: تذهب للموظف وتفتح تذكرة، وهو ما يريده النزيل منها أصلًا.
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


SHORTCUT_QUESTION = {key: text for key, _icon, _label, text in DEFAULT_SHORTCUTS}


def shortcuts_for(tenant: Tenant) -> list[dict]:
    return [{"key": k, "icon": i, "label": lbl, "text": t}
            for k, i, lbl, t in DEFAULT_SHORTCUTS]
