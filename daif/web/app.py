"""تطبيق FastAPI: لوحة التحكم + webhook واتساب."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..assistant import Assistant
from ..clock import now_riyadh, parse_date
from ..config import get_settings
from ..db import get_session, init_db, session_scope
from ..knowledge import KnowledgeError
from ..models import Fact, Guest, HandoffRecord, StaffUser, Tenant, Ticket
from ..repository import (
    audit,
    conversation_history,
    get_tenant,
    knowledge_gaps,
    list_facts,
    list_guests,
    list_handoffs,
    list_messages,
    list_tickets,
    load_knowledge_base,
    next_fact_key,
    staff_by_email,
    stats,
    tenant_by_phone_number_id,
)
from ..security import verify_password
from ..service import build_context, handle_inbound
from ..whatsapp import client_for_tenant, parse_webhook, verify_signature, verify_subscription
from . import auth
from .i18n import LOCALES, Translator, get_translator

logger = logging.getLogger("daif.web")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="ضيف — Daif", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_assistant = Assistant()

# أسماء اللغات كما تُعرض في لوحة الإحصائيات
LANGUAGE_NAMES = {
    "ar": "العربية", "en": "English", "id": "Indonesia", "ms": "Melayu",
    "tr": "Türkçe", "ur": "اردو", "bn": "বাংলা", "fa": "فارسی",
    "fr": "Français", "ha": "Hausa",
}

TICKET_TYPE_LABELS = {
    "cleaning": "تنظيف", "towels": "مناشف", "amenities": "مستلزمات",
    "maintenance": "صيانة", "laundry": "غسيل", "late_checkout": "تأخير خروج",
    "luggage": "أمتعة", "wake_up": "إيقاظ", "transport": "نقل", "other": "أخرى",
}


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# الاعتماديات
# ---------------------------------------------------------------------------

class Principal:
    """الموظف الحالي والفندق الذي يملكه."""

    def __init__(self, user: StaffUser, tenant: Tenant, t: Translator) -> None:
        self.user = user
        self.tenant = tenant
        self.t = t


def _locale_for(request: Request, user: StaffUser | None) -> str:
    override = request.query_params.get("lang")
    if override and override in LOCALES:
        return override
    if user and user.locale in LOCALES:
        return user.locale
    return get_settings().default_locale


def current_principal(
    request: Request, session: Session = Depends(get_session)
) -> Principal | None:
    ident = auth.read(request.cookies.get(auth.COOKIE_NAME))
    if ident is None:
        return None
    staff_id, tenant_id = ident
    user = session.get(StaffUser, staff_id)
    if user is None or not user.active or user.tenant_id != tenant_id:
        return None
    tenant = get_tenant(session, tenant_id)
    if tenant is None:
        return None
    return Principal(user, tenant, get_translator(_locale_for(request, user)))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _render(
    request: Request,
    session: Session,
    principal: Principal,
    template: str,
    page: str,
    **context,
) -> HTMLResponse:
    """يجمع سياق القالب المشترك (الترجمة، عدّادات القائمة، المستخدم)."""
    base = {
        "t": principal.t,
        "locales": LOCALES,
        "user": principal.user,
        "tenant": principal.tenant,
        "page": page,
        "open_tickets": len(list_tickets(session, principal.tenant.id, status="open", limit=999)),
        "open_handoffs": len(list_handoffs(session, principal.tenant.id, status="open", limit=999)),
        "flash": request.query_params.get("ok") and principal.t("saved"),
        "flash_kind": "ok",
    }
    base.update(context)
    return templates.TemplateResponse(request, template, base)


# ---------------------------------------------------------------------------
# الدخول والخروج
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    t = get_translator(_locale_for(request, None))
    return templates.TemplateResponse(
        request, "login.html", {"t": t, "locales": LOCALES, "error": False}
    )


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    user = staff_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        t = get_translator(_locale_for(request, None))
        return templates.TemplateResponse(
            request,
            "login.html",
            {"t": t, "locales": LOCALES, "error": True, "email": email},
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(auth.COOKIE_NAME, auth.issue(user.id, user.tenant_id), **auth.cookie_kwargs())
    return response


@app.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# نظرة عامة
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def overview(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    tenant_id = principal.tenant.id
    figures = stats(session, tenant_id)
    recent = list_messages(session, tenant_id, limit=15)
    guests = {g.id: (g.room or g.wa_id[-4:]) for g in list_guests(session, tenant_id, limit=500)}
    total_input = figures["input_tokens"] + figures["cache_read_tokens"]
    cache_rate = round(figures["cache_read_tokens"] / total_input * 100) if total_input else 0
    return _render(
        request, session, principal, "overview.html", "overview",
        stats=figures,
        recent=recent,
        guest_labels=guests,
        language_names=LANGUAGE_NAMES,
        cache_rate=cache_rate,
        fact_count=len(list_facts(session, tenant_id, only_active=True)),
    )


# ---------------------------------------------------------------------------
# قاعدة المعرفة
# ---------------------------------------------------------------------------

@app.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    from_gap: str = Query(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "knowledge.html", "knowledge",
        facts=list_facts(session, principal.tenant.id),
        next_key=next_fact_key(session, principal.tenant.id),
        today=date.today(),
        prefill=from_gap,
    )


def _fact_fields(form: dict) -> dict:
    seasons = form.get("seasons") or ["normal", "ramadan", "hajj"]
    if isinstance(seasons, str):
        seasons = [seasons]
    return {
        "text": (form.get("text") or "").strip(),
        "topic": (form.get("topic") or "").strip(),
        "seasons": ",".join(s for s in seasons if s in ("normal", "ramadan", "hajj")),
        "hours": (form.get("hours") or "").strip() or None,
        "valid_until": parse_date(form.get("valid_until") or ""),
        "paid": bool(form.get("paid")),
        "active": bool(form.get("active")),
    }


@app.post("/knowledge/create")
async def knowledge_create(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    form = await request.form()
    data = _fact_fields({**form, "seasons": form.getlist("seasons")})
    key = (form.get("key") or "").strip() or next_fact_key(session, principal.tenant.id)
    fact = Fact(
        tenant_id=principal.tenant.id, key=key, updated_by=principal.user.email, **data
    )
    session.add(fact)
    session.flush()
    _validate_or_raise(session, principal.tenant.id)
    audit(session, principal.tenant.id, principal.user.email, "fact.create", entity="fact", entity_id=key)
    return RedirectResponse("/knowledge?ok=1", status_code=303)


@app.post("/knowledge/{fact_id}/update")
async def knowledge_update(
    fact_id: int,
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    fact = session.get(Fact, fact_id)
    # حاجز العزل: لا يُعدَّل سجل لا يخص فندق الموظف.
    if fact is None or fact.tenant_id != principal.tenant.id:
        return RedirectResponse("/knowledge", status_code=303)
    form = await request.form()
    for field, value in _fact_fields({**form, "seasons": form.getlist("seasons")}).items():
        setattr(fact, field, value)
    fact.updated_by = principal.user.email
    session.flush()
    _validate_or_raise(session, principal.tenant.id)
    audit(session, principal.tenant.id, principal.user.email, "fact.update", entity="fact", entity_id=fact.key)
    return RedirectResponse("/knowledge?ok=1", status_code=303)


@app.post("/knowledge/{fact_id}/delete")
def knowledge_delete(
    fact_id: int,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    fact = session.get(Fact, fact_id)
    if fact is not None and fact.tenant_id == principal.tenant.id:
        audit(session, principal.tenant.id, principal.user.email, "fact.delete", entity="fact", entity_id=fact.key)
        session.delete(fact)
    return RedirectResponse("/knowledge?ok=1", status_code=303)


def _validate_or_raise(session: Session, tenant_id: int) -> None:
    """قاعدة معرفة فاسدة تُرفض عند الحفظ، لا عند أول نزيل."""
    try:
        load_knowledge_base(session, tenant_id)
    except KnowledgeError:
        session.rollback()
        raise


# ---------------------------------------------------------------------------
# التذاكر والتحويلات
# ---------------------------------------------------------------------------

@app.get("/tickets", response_class=HTMLResponse)
def tickets_page(
    request: Request,
    status: str = Query(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "tickets.html", "tickets",
        tickets=list_tickets(session, principal.tenant.id, status=status or None),
        status=status,
        ticket_types=TICKET_TYPE_LABELS,
    )


@app.post("/tickets/{ticket_id}/status")
def ticket_status(
    ticket_id: int,
    status: str = Form(...),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    ticket = session.get(Ticket, ticket_id)
    if ticket is not None and ticket.tenant_id == principal.tenant.id:
        if status in ("open", "in_progress", "done", "cancelled"):
            ticket.status = status
            ticket.assigned_to = principal.user.email
            ticket.closed_at = now_riyadh() if status in ("done", "cancelled") else None
    return RedirectResponse("/tickets", status_code=303)


@app.get("/handoffs", response_class=HTMLResponse)
def handoffs_page(
    request: Request,
    status: str = Query(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "handoffs.html", "handoffs",
        handoffs=list_handoffs(session, principal.tenant.id, status=status or None),
        status=status,
    )


@app.post("/handoffs/{handoff_id}/resolve")
def handoff_resolve(
    handoff_id: int,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    record = session.get(HandoffRecord, handoff_id)
    if record is not None and record.tenant_id == principal.tenant.id:
        record.status = "resolved"
        record.resolved_by = principal.user.email
        record.resolved_at = now_riyadh()
    return RedirectResponse("/handoffs", status_code=303)


# ---------------------------------------------------------------------------
# المحادثات والفجوات
# ---------------------------------------------------------------------------

@app.get("/conversations", response_class=HTMLResponse)
def conversations_page(
    request: Request,
    guest: int | None = Query(default=None),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    guests = list_guests(session, principal.tenant.id)
    selected = None
    messages: list = []
    if guest is not None:
        record = session.get(Guest, guest)
        if record is not None and record.tenant_id == principal.tenant.id:
            selected = record
            messages = list(reversed(list_messages(session, principal.tenant.id, guest_id=guest, limit=60)))
    return _render(
        request, session, principal, "conversations.html", "conversations",
        guests=guests, selected=selected, messages=messages,
    )


@app.get("/gaps", response_class=HTMLResponse)
def gaps_page(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "gaps.html", "gaps",
        gaps=knowledge_gaps(session, principal.tenant.id),
    )


# ---------------------------------------------------------------------------
# المحاكي
# ---------------------------------------------------------------------------

@app.get("/simulator", response_class=HTMLResponse)
def simulator_page(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "simulator.html", "simulator",
        message="", room="", season=principal.tenant.season,
        desk_status=principal.tenant.desk_status, result=None,
    )


@app.post("/simulator", response_class=HTMLResponse)
def simulator_run(
    request: Request,
    message: str = Form(...),
    room: str = Form(default=""),
    season: str = Form(default="normal"),
    desk_status: str = Form(default="staffed"),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    probe = Guest(tenant_id=principal.tenant.id, wa_id="simulator", room=room.strip())
    ctx = replace(
        build_context(principal.tenant, probe), season=season, desk_status=desk_status
    )
    kb = load_knowledge_base(session, principal.tenant.id)
    result = _assistant.reply(ctx=ctx, kb=kb, message=message)
    return _render(
        request, session, principal, "simulator.html", "simulator",
        message=message, room=room, season=season, desk_status=desk_status,
        result=result,
        result_json=json.dumps(result.reply.model_dump(), ensure_ascii=False, indent=2),
    )


# ---------------------------------------------------------------------------
# الإعدادات
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(request, session, principal, "settings.html", "settings")


@app.post("/settings")
def settings_save(
    name: str = Form(...),
    season: str = Form(...),
    hk_window: str = Form(...),
    desk_status: str = Form(...),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    tenant = principal.tenant
    tenant.name = name.strip() or tenant.name
    if season in ("normal", "ramadan", "hajj"):
        tenant.season = season
    if desk_status in ("staffed", "thin", "unstaffed"):
        tenant.desk_status = desk_status
    tenant.hk_window = hk_window.strip() or tenant.hk_window
    audit(session, tenant.id, principal.user.email, "settings.update", detail=f"{season}/{desk_status}")
    return RedirectResponse("/settings?ok=1", status_code=303)


@app.post("/settings/whatsapp")
def settings_whatsapp(
    wa_phone_number_id: str = Form(default=""),
    wa_access_token: str = Form(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    tenant = principal.tenant
    tenant.wa_phone_number_id = wa_phone_number_id.strip()
    # حقل فارغ يعني «لا تغيّر الرمز المحفوظ»
    if wa_access_token.strip():
        tenant.wa_access_token = wa_access_token.strip()
    audit(session, tenant.id, principal.user.email, "settings.whatsapp")
    return RedirectResponse("/settings?ok=1", status_code=303)


@app.post("/settings/locale")
def settings_locale(
    locale: str = Form(...),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    if locale in LOCALES:
        principal.user.locale = locale
    return RedirectResponse("/settings?ok=1", status_code=303)


# ---------------------------------------------------------------------------
# webhook واتساب
# ---------------------------------------------------------------------------

@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def webhook_verify(request: Request) -> Response:
    """تحقق الاشتراك الأولي من Meta."""
    params = request.query_params
    challenge = verify_subscription(
        params.get("hub.mode"), params.get("hub.verify_token"), params.get("hub.challenge")
    )
    if challenge is None:
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(challenge)


@app.post("/webhook/whatsapp")
async def webhook_receive(request: Request, background: BackgroundTasks) -> Response:
    """يستقبل الرسائل. يردّ 200 فورًا ويعالج في الخلفية."""
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_signature(body, signature, get_settings().wa_app_secret):
        return PlainTextResponse("invalid signature", status_code=403)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return PlainTextResponse("bad json", status_code=400)

    parsed = parse_webhook(payload)
    for inbound in parsed.messages:
        background.add_task(_process_inbound, inbound)
    return PlainTextResponse("ok")


def _process_inbound(inbound) -> None:
    """معالجة رسالة واحدة خارج دورة الطلب."""
    try:
        with session_scope() as session:
            tenant = tenant_by_phone_number_id(session, inbound.phone_number_id)
            if tenant is None:
                logger.warning("رسالة لرقم غير مسجّل: %s", inbound.phone_number_id)
                return

            text = inbound.text
            if inbound.has_media and text:
                text = f"{text}\n[أرفق النزيل صورة]"

            outcome = handle_inbound(
                session,
                tenant,
                wa_id=inbound.wa_id,
                text=text,
                assistant=_assistant,
                wa_message_id=inbound.message_id,
                low_confidence_input=inbound.low_confidence,
            )
            if inbound.profile_name and not outcome.guest.name:
                outcome.guest.name = inbound.profile_name

            reply_text = outcome.reply_text
            escalate = outcome.escalate
            tenant_token = tenant.wa_access_token
            phone_id = tenant.wa_phone_number_id

        if not reply_text.strip() or not tenant_token or not phone_id:
            return

        from ..whatsapp import WhatsAppClient

        client = WhatsAppClient(access_token=tenant_token, phone_number_id=phone_id)
        client.mark_read(inbound.message_id)
        client.send_text(inbound.wa_id, reply_text, reply_to=inbound.message_id)

        if escalate:
            logger.error(
                "تصعيد عاجل: الاستقبال غير مشغّل وطلب عاجل من %s", inbound.wa_id
            )
    except Exception:  # noqa: BLE001
        logger.exception("فشل معالجة رسالة واردة من %s", inbound.wa_id)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"
