"""تطبيق FastAPI: لوحة التحكم + webhook واتساب."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import apikeys, authz
from ..assistant import Assistant
from ..clock import now_riyadh, parse_date
from ..config import get_settings
from ..crypto import decrypt, encrypt
from ..db import get_session, init_db, session_scope
from ..escalation import build_payload, notify
from .. import billing
from ..knowledge import KnowledgeError
from ..plans import CATALOG, FEATURE_NAMES, format_sar, get as get_plan
from ..reports import (
    handoffs_csv,
    invoices_csv,
    shift_report,
    shift_report_text,
    tickets_csv,
)
from ..ratelimit import LOGIN, WEBHOOK, limiter
from ..models import Fact, Guest, HandoffRecord, StaffUser, Tenant, Ticket
from ..repository import (
    audit,
    count_active_owners,
    count_unverified_guests,
    expiring_facts,
    conversation_history,
    get_tenant,
    knowledge_gaps,
    list_facts,
    list_guests,
    list_handoffs,
    list_messages,
    list_staff,
    list_tickets,
    load_knowledge_base,
    next_fact_key,
    onboarding_state,
    search_facts,
    staff_by_email,
    stats,
    tenant_by_phone_number_id,
)
from ..security import hash_password, verify_password
from ..service import build_context, handle_inbound
from ..whatsapp import client_for_tenant, parse_webhook, verify_signature, verify_subscription
from . import auth, csrf
from .i18n import LOCALES, Translator, get_translator

logger = logging.getLogger("daif.web")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# اعتمادية CSRF تُطبَّق على كل المسارات؛ تتجاهل الآمنة منها والـwebhook.
app = FastAPI(
    title="ضيف — Daif",
    docs_url=None,
    redoc_url=None,
    dependencies=[Depends(csrf.guard)],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# الخطوط من Google Fonts، وكل ما عداها من أصل التطبيق نفسه.
_CSP = (
    "default-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """ترويسات أمان على كل رد."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", _CSP)
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

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


from .api import router as _api_router  # noqa: E402
from .platform import setup as _platform_setup  # noqa: E402

app.include_router(_platform_setup(templates))
app.include_router(_api_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    _bootstrap_admin()


def _bootstrap_admin() -> None:
    """ينشئ أول مشغّل للمنصة من متغيّرات البيئة، مرة واحدة.

    على منصات مثل Railway لا توجد طرفية جاهزة عند أول نشر. بدون هذا يصير
    النظام يعمل ولا أحد يستطيع الدخول إليه. لا يعمل إلا إذا لم يوجد مشغّل
    أصلًا — فإعادة النشر لا تُنشئ حسابًا ثانيًا ولا تُعيد ضبط كلمة مرور.
    """
    import os

    from sqlalchemy import select

    from ..models import PlatformAdmin
    from ..security import hash_password

    email = os.environ.get("DAIF_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("DAIF_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        return
    if len(password) < 12:
        logger.error("كلمة مرور التهيئة أقصر من ١٢ محرفًا — لم يُنشأ حساب")
        return
    try:
        with session_scope() as session:
            if session.scalar(select(PlatformAdmin).limit(1)) is not None:
                return
            session.add(
                PlatformAdmin(
                    email=email, name="مشغّل المنصة", password_hash=hash_password(password)
                )
            )
            logger.info("أُنشئ أول مشغّل للمنصة: %s", email)
    except Exception:  # noqa: BLE001
        logger.exception("فشلت تهيئة أول مشغّل")


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


# صلاحية العرض المطلوبة لكل صفحة — الفحص في مكان واحد لا في كل مسار.
PAGE_PERMISSION: dict[str, str] = {
    "overview": authz.VIEW_OVERVIEW,
    "guests": authz.VIEW_GUESTS,
    "knowledge": authz.VIEW_KNOWLEDGE,
    "tickets": authz.VIEW_TICKETS,
    "handoffs": authz.VIEW_HANDOFFS,
    "conversations": authz.VIEW_CONVERSATIONS,
    "gaps": authz.VIEW_GAPS,
    "simulator": authz.VIEW_SIMULATOR,
    "settings": authz.VIEW_SETTINGS,
    "billing": authz.VIEW_BILLING,
    "team": authz.WRITE_USERS,
}


def _require(principal: Principal, permission: str) -> None:
    """يمنع ما لا يملكه الدور. الصلاحية تُمنح صراحةً وما عداها ممنوع."""
    if not authz.can(principal.user.role, permission):
        raise HTTPException(status_code=403, detail="لا تملك صلاحية هذا الإجراء")


def _template(request: Request, name: str, context: dict, status_code: int = 200):
    """يبني الرد ويثبّت رمز حماية النماذج."""
    token = csrf.token_for(request)
    context["csrf_token"] = token
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    csrf.attach(request, response, token)
    return response


def _render(
    request: Request,
    session: Session,
    principal: Principal,
    template: str,
    page: str,
    **context,
) -> HTMLResponse:
    """يجمع سياق القالب المشترك، ويفحص صلاحية عرض الصفحة."""
    required = PAGE_PERMISSION.get(page)
    if required:
        _require(principal, required)
    base = {
        "t": principal.t,
        "locales": LOCALES,
        "user": principal.user,
        "tenant": principal.tenant,
        "page": page,
        "open_tickets": len(list_tickets(session, principal.tenant.id, status="open", limit=999)),
        "open_handoffs": len(list_handoffs(session, principal.tenant.id, status="open", limit=999)),
        "unverified_guests": count_unverified_guests(session, principal.tenant.id),
        "flash": request.query_params.get("ok") and principal.t("saved"),
        "flash_kind": "ok",
    }
    base.update(context)
    base["can"] = lambda permission: authz.can(principal.user.role, permission)
    return _template(request, template, base)


# ---------------------------------------------------------------------------
# الدخول والخروج
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    t = get_translator(_locale_for(request, None))
    return _template(request, "login.html", {"t": t, "locales": LOCALES, "error": False})


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    # التخمين المتكرر لكلمة المرور محدود بالعنوان وبالبريد معًا.
    client_ip = request.client.host if request.client else "?"
    allowed = limiter.hit(f"login:{client_ip}", LOGIN) and limiter.hit(
        f"login:{email.strip().lower()}", LOGIN
    )
    t = get_translator(_locale_for(request, None))
    if not allowed:
        logger.warning("تجاوز محاولات الدخول من %s", client_ip)
        return _template(
            request,
            "login.html",
            {"t": t, "locales": LOCALES, "error": True, "email": email, "throttled": True},
            status_code=429,
        )

    user = staff_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return _template(
            request,
            "login.html",
            {"t": t, "locales": LOCALES, "error": True, "email": email},
            status_code=401,
        )
    # دخول ناجح يمسح العدّاد حتى لا يُعاقب المستخدم الشرعي.
    limiter.reset(f"login:{email.strip().lower()}")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.issue(user.id, user.tenant_id),
        **auth.cookie_kwargs(secure=request.url.scheme == "https"),
    )
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
        # الجذر العام صفحة تعريف، لا تحويلًا لصفحة دخول: الزائر الأول
        # ليس موظف فندق مشترك.
        return _template(request, "landing.html", {
            "t": get_translator(_locale_for(request, None)),
            "locales": LOCALES,
            "plans": [p for p in CATALOG.values() if p.code != "trial"],
            "feature_names": FEATURE_NAMES,
            "money": format_sar,
        })
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
        expiring=expiring_facts(session, tenant_id),
        onboarding=onboarding_state(session, principal.tenant),
        today=date.today(),
        effective_season=principal.tenant.effective_season(date.today()),
    )


# ---------------------------------------------------------------------------
# قاعدة المعرفة
# ---------------------------------------------------------------------------

@app.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    from_gap: str = Query(default=""),
    q: str = Query(default=""),
    season: str = Query(default=""),
    status: str = Query(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "knowledge.html", "knowledge",
        facts=search_facts(
            session, principal.tenant.id, query=q, season=season, status=status
        ),
        q=q,
        season_filter=season,
        status_filter=status,
        next_key=next_fact_key(session, principal.tenant.id),
        today=date.today(),
        prefill=from_gap,
        expiring=expiring_facts(session, principal.tenant.id),
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
    _require(principal, authz.WRITE_KNOWLEDGE)
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
    _require(principal, authz.WRITE_KNOWLEDGE)
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
    _require(principal, authz.WRITE_KNOWLEDGE)
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
# النزلاء والغرف
# ---------------------------------------------------------------------------

@app.get("/guests", response_class=HTMLResponse)
def guests_page(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    guests = list_guests(session, principal.tenant.id, limit=400)
    return _render(
        request, session, principal, "guests.html", "guests",
        guests=guests,
        unverified_count=sum(1 for g in guests if not g.room_verified),
    )


@app.post("/guests/{guest_id}/update")
def guest_update(
    guest_id: int,
    name: str = Form(default=""),
    room: str = Form(default=""),
    group_mode: str = Form(default="individual"),
    group_rooms: str = Form(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_GUESTS)
    guest = session.get(Guest, guest_id)
    if guest is None or guest.tenant_id != principal.tenant.id:
        return RedirectResponse("/guests", status_code=303)
    guest.name = name.strip()
    guest.room = room.strip()
    guest.group_mode = group_mode if group_mode in ("individual", "group_leader") else "individual"
    # قائمة الغرف بلا معنى خارج وضع المطوّف — تُفرَّغ حتى لا تمنح صلاحية صامتة.
    guest.group_rooms = group_rooms.strip() if guest.group_mode == "group_leader" else ""
    audit(
        session, principal.tenant.id, principal.user.email, "guest.update",
        entity="guest", entity_id=guest.wa_id,
        detail=f"room={guest.room} mode={guest.group_mode} rooms={guest.group_rooms}",
    )
    return RedirectResponse("/guests?ok=1", status_code=303)


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
    _require(principal, authz.WRITE_TICKETS)
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
    _require(principal, authz.WRITE_HANDOFFS)
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
# الفريق
# ---------------------------------------------------------------------------

@app.get("/team", response_class=HTMLResponse)
def team_page(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    return _render(
        request, session, principal, "team.html", "team",
        staff=list_staff(session, principal.tenant.id),
        error=request.query_params.get("err", ""),
    )


@app.post("/team/create")
def team_create(
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(default=""),
    role: str = Form(default="staff"),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_USERS)
    if len(password) < 8 or role not in authz.ROLES:
        return RedirectResponse("/team?err=login_error", status_code=303)
    if staff_by_email(session, email):
        return RedirectResponse("/team?err=login_error", status_code=303)
    session.add(
        StaffUser(
            tenant_id=principal.tenant.id,
            email=email.strip().lower(),
            name=name.strip(),
            password_hash=hash_password(password),
            role=role,
            locale=principal.user.locale,
        )
    )
    audit(
        session, principal.tenant.id, principal.user.email, "staff.create",
        entity="staff", entity_id=email.strip().lower(), detail=f"role={role}",
    )
    return RedirectResponse("/team?ok=1", status_code=303)


def _own_tenant_staff(session: Session, principal: Principal, staff_id: int) -> StaffUser | None:
    record = session.get(StaffUser, staff_id)
    if record is None or record.tenant_id != principal.tenant.id:
        return None
    return record


@app.post("/team/{staff_id}/role")
def team_role(
    staff_id: int,
    role: str = Form(...),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_USERS)
    # لا يغيّر أحد دور نفسه: أسرع طريق لقفل الفندق خارج حسابه
    if staff_id == principal.user.id:
        return RedirectResponse("/team?err=cannot_change_self", status_code=303)
    record = _own_tenant_staff(session, principal, staff_id)
    if record is None or role not in authz.ROLES:
        return RedirectResponse("/team", status_code=303)
    # آخر مالك نشط لا يُنزَّل
    if record.role == "owner" and role != "owner" and count_active_owners(session, principal.tenant.id) <= 1:
        return RedirectResponse("/team?err=last_owner_warning", status_code=303)
    record.role = role
    audit(
        session, principal.tenant.id, principal.user.email, "staff.role",
        entity="staff", entity_id=record.email, detail=f"role={role}",
    )
    return RedirectResponse("/team?ok=1", status_code=303)


@app.post("/team/{staff_id}/toggle")
def team_toggle(
    staff_id: int,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_USERS)
    if staff_id == principal.user.id:
        return RedirectResponse("/team?err=cannot_change_self", status_code=303)
    record = _own_tenant_staff(session, principal, staff_id)
    if record is None:
        return RedirectResponse("/team", status_code=303)
    if (
        record.active
        and record.role == "owner"
        and count_active_owners(session, principal.tenant.id) <= 1
    ):
        return RedirectResponse("/team?err=last_owner_warning", status_code=303)
    record.active = not record.active
    audit(
        session, principal.tenant.id, principal.user.email, "staff.toggle",
        entity="staff", entity_id=record.email, detail=f"active={record.active}",
    )
    return RedirectResponse("/team?ok=1", status_code=303)


# ---------------------------------------------------------------------------
# الفوترة
# ---------------------------------------------------------------------------

@app.get("/billing", response_class=HTMLResponse)
def billing_page(
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    tenant = principal.tenant
    return _render(
        request, session, principal, "billing.html", "billing",
        plan=get_plan(tenant.plan),
        quota=billing.quota(session, tenant),
        invoices=billing.list_invoices(session, tenant.id),
        catalog=[p for p in CATALOG.values() if p.code != "trial" or tenant.plan == "trial"],
        feature_names=FEATURE_NAMES,
        rooms_exceeded=billing.rooms_exceeded(tenant),
        money=format_sar,
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
    return _render(
        request, session, principal, "settings.html", "settings",
        effective_season=principal.tenant.effective_season(date.today()),
        api_keys=apikeys.list_keys(session, principal.tenant.id)
        if authz.can(principal.user.role, authz.WRITE_WHATSAPP)
        else [],
        new_key=request.query_params.get("key", ""),
    )


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
    _require(principal, authz.WRITE_SETTINGS)
    tenant = principal.tenant
    tenant.name = name.strip() or tenant.name
    if season in ("normal", "ramadan", "hajj"):
        tenant.season = season
    if desk_status in ("staffed", "thin", "unstaffed"):
        tenant.desk_status = desk_status
    tenant.hk_window = hk_window.strip() or tenant.hk_window
    audit(session, tenant.id, principal.user.email, "settings.update", detail=f"{season}/{desk_status}")
    return RedirectResponse("/settings?ok=1", status_code=303)


@app.post("/settings/seasons")
def settings_seasons(
    season_auto: str = Form(default=""),
    ramadan_start: str = Form(default=""),
    ramadan_end: str = Form(default=""),
    hajj_start: str = Form(default=""),
    hajj_end: str = Form(default=""),
    escalation_webhook: str = Form(default=""),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_SETTINGS)
    tenant = principal.tenant
    tenant.season_auto = bool(season_auto)
    tenant.ramadan_start = parse_date(ramadan_start)
    tenant.ramadan_end = parse_date(ramadan_end)
    tenant.hajj_start = parse_date(hajj_start)
    tenant.hajj_end = parse_date(hajj_end)
    tenant.escalation_webhook = escalation_webhook.strip()
    audit(session, tenant.id, principal.user.email, "settings.seasons",
          detail=f"auto={tenant.season_auto}")
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
    _require(principal, authz.WRITE_WHATSAPP)
    tenant = principal.tenant
    tenant.wa_phone_number_id = wa_phone_number_id.strip()
    # حقل فارغ يعني «لا تغيّر الرمز المحفوظ». الرمز يُخزَّن مشفّرًا دائمًا.
    if wa_access_token.strip():
        tenant.wa_access_token = encrypt(wa_access_token.strip())
    audit(session, tenant.id, principal.user.email, "settings.whatsapp")
    return RedirectResponse("/settings?ok=1", status_code=303)


@app.post("/settings/api-keys")
def create_api_key(
    name: str = Form(...),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    """ينشئ مفتاح ربط. يُعرض المفتاح مرة واحدة ثم لا يُسترجَع أبدًا."""
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_WHATSAPP)
    issued = apikeys.issue(
        session, principal.tenant.id, name=name, created_by=principal.user.email
    )
    audit(
        session, principal.tenant.id, principal.user.email, "apikey.create",
        entity="api_key", entity_id=issued.record.prefix,
    )
    return RedirectResponse(f"/settings?key={issued.token}", status_code=303)


@app.post("/settings/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    if principal is None:
        return _login_redirect()
    _require(principal, authz.WRITE_WHATSAPP)
    if apikeys.revoke(session, principal.tenant.id, key_id):
        audit(
            session, principal.tenant.id, principal.user.email, "apikey.revoke",
            entity="api_key", entity_id=str(key_id),
        )
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
    client_ip = request.client.host if request.client else "?"
    if not limiter.hit(f"webhook:{client_ip}", WEBHOOK):
        logger.warning("تجاوز معدّل نداءات webhook من %s", client_ip)
        return PlainTextResponse("rate limited", status_code=429)

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
            tenant_token = decrypt(tenant.wa_access_token)
            phone_id = tenant.wa_phone_number_id
            escalation_url = tenant.escalation_webhook
            escalation_payload = (
                build_payload(
                    tenant.name,
                    outcome.ticket.room if outcome.ticket else "",
                    outcome.ticket.detail if outcome.ticket else "",
                    outcome.ticket.urgency if outcome.ticket else "urgent",
                    inbound.wa_id,
                )
                if escalate
                else None
            )

        if not reply_text.strip() or not tenant_token or not phone_id:
            return

        from ..whatsapp import WhatsAppClient

        client = WhatsAppClient(access_token=tenant_token, phone_number_id=phone_id)
        client.mark_read(inbound.message_id)
        client.send_text(inbound.wa_id, reply_text, reply_to=inbound.message_id)

        if escalate and escalation_payload is not None:
            # الرد وصل النزيل أصلًا؛ فشل التصعيد يُسجَّل ولا يُسقط المعالجة.
            delivered = notify(escalation_url, escalation_payload)
            logger.log(
                logging.INFO if delivered else logging.ERROR,
                "تصعيد عاجل من %s — %s",
                inbound.wa_id,
                "أُرسل" if delivered else "تعذّر الإرسال",
            )
    except Exception:  # noqa: BLE001
        logger.exception("فشل معالجة رسالة واردة من %s", inbound.wa_id)


# ---------------------------------------------------------------------------
# التصدير والتقارير
# ---------------------------------------------------------------------------

def _csv_response(body: str, filename: str) -> PlainTextResponse:
    return PlainTextResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/{kind}.csv")
def export_csv(
    kind: str,
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    """تصدير بيانات الفندق. بياناته ملكه — التصدير حق لا ميزة."""
    if principal is None:
        return _login_redirect()
    tenant_id = principal.tenant.id
    exporters = {
        "tickets": (authz.VIEW_TICKETS, tickets_csv),
        "handoffs": (authz.VIEW_HANDOFFS, handoffs_csv),
        "invoices": (authz.VIEW_BILLING, invoices_csv),
    }
    if kind not in exporters:
        raise HTTPException(status_code=404, detail="نوع تصدير غير معروف")
    permission, exporter = exporters[kind]
    _require(principal, permission)
    audit(session, tenant_id, principal.user.email, "export", entity=kind)
    return _csv_response(
        exporter(session, tenant_id), f"daif-{principal.tenant.slug}-{kind}.csv"
    )


@app.get("/reports/shift", response_class=PlainTextResponse)
def shift(
    hours: int = Query(default=12, ge=1, le=48),
    session: Session = Depends(get_session),
    principal: Principal | None = Depends(current_principal),
) -> Response:
    """تقرير الوردية نصًا — يُنسخ ويُرسل لمدير المناوبة كما هو."""
    if principal is None:
        return _login_redirect()
    _require(principal, authz.VIEW_OVERVIEW)
    report = shift_report(session, principal.tenant.id, hours=hours)
    return PlainTextResponse(shift_report_text(report, principal.tenant.name))


# ---------------------------------------------------------------------------
# الصحة والمقاييس
# ---------------------------------------------------------------------------

@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/readyz", response_class=PlainTextResponse)
def readyz(session: Session = Depends(get_session)) -> Response:
    """جاهزية حقيقية: قاعدة البيانات تستجيب."""
    from sqlalchemy import text

    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logger.exception("فحص الجاهزية فشل")
        return PlainTextResponse("database unavailable", status_code=503)
    return PlainTextResponse("ready")
