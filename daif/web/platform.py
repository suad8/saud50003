"""لوحة مشغّل المنصة.

منفصلة تمامًا عن لوحة الفندق: كوكي مختلف، وملح توقيع مختلف، وجدول حسابات
مختلف. اختراق حساب فندق واحد يجب ألا يمنح شيئًا على مستوى المنصة.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from .. import billing
from ..clock import now_riyadh
from ..db import get_session
from ..knowledge import KnowledgeBase
from ..models import Fact, PlatformAdmin, StaffUser, Tenant
from ..plans import CATALOG, format_sar, get as get_plan
from ..repository import (
    audit,
    list_tenants,
    platform_admin_by_email,
    platform_stats,
    staff_by_email,
    tenant_by_slug,
)
from ..security import hash_password, verify_password
from . import auth, csrf
from .i18n import LOCALES, get_translator

logger = logging.getLogger("daif.platform")

router = APIRouter(prefix="/platform")

COOKIE = "daif_platform"
_MAX_AGE = 60 * 60 * 8


def _serializer() -> URLSafeSerializer:
    # نفس السرّ، وملح مختلف: توقيع إحدى اللوحتين لا يصلح للأخرى.
    return URLSafeSerializer(auth.signing_secret(), salt="daif-platform-console")


def issue(admin_id: int) -> str:
    return _serializer().dumps({"a": admin_id})


def read(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(_serializer().loads(raw)["a"])
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


def current_admin(
    request: Request, session: Session = Depends(get_session)
) -> PlatformAdmin | None:
    admin_id = read(request.cookies.get(COOKIE))
    if admin_id is None:
        return None
    admin = session.get(PlatformAdmin, admin_id)
    return admin if admin is not None and admin.active else None


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/platform/login", status_code=303)


# ---------------------------------------------------------------------------

def setup(templates) -> APIRouter:
    """يربط الموجّه بمحرّك القوالب. يُنادى مرة واحدة من `app.py`."""

    def render(request: Request, name: str, context: dict, status_code: int = 200):
        token = csrf.token_for(request)
        context.setdefault("csrf_token", token)
        context.setdefault("locales", LOCALES)
        response = templates.TemplateResponse(request, name, context, status_code=status_code)
        csrf.attach(request, response, token)
        return response

    @router.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        t = get_translator(request.query_params.get("lang") or "ar")
        return render(request, "platform_login.html", {"t": t, "error": False})

    @router.post("/login")
    def login_submit(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        session: Session = Depends(get_session),
    ) -> Response:
        from ..ratelimit import LOGIN, limiter

        client_ip = request.client.host if request.client else "?"
        t = get_translator("ar")
        if not limiter.hit(f"platform-login:{client_ip}", LOGIN):
            logger.warning("تجاوز محاولات دخول لوحة المنصة من %s", client_ip)
            return render(request, "platform_login.html", {"t": t, "error": True}, 429)

        admin = platform_admin_by_email(session, email)
        if admin is None or not verify_password(password, admin.password_hash):
            logger.warning("محاولة دخول فاشلة للوحة المنصة: %s", email)
            return render(request, "platform_login.html", {"t": t, "error": True}, 401)

        admin.last_login_at = now_riyadh()
        response = RedirectResponse("/platform", status_code=303)
        response.set_cookie(
            COOKIE, issue(admin.id), httponly=True, samesite="lax", max_age=_MAX_AGE, path="/"
        )
        return response

    @router.post("/logout")
    def logout() -> Response:
        response = RedirectResponse("/platform/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse)
    def tenants_page(
        request: Request,
        session: Session = Depends(get_session),
        admin: PlatformAdmin | None = Depends(current_admin),
    ) -> Response:
        if admin is None:
            return _login_redirect()
        period = billing.period_of()
        tenants = list_tenants(session)
        rows = []
        for tenant in tenants:
            state = billing.quota(session, tenant, period)
            rows.append({
                "tenant": tenant,
                "plan": get_plan(tenant.plan),
                "quota": state,
                "rooms_exceeded": billing.rooms_exceeded(tenant),
            })
        return render(request, "platform_tenants.html", {
            "t": get_translator(admin.locale),
            "admin": admin,
            "rows": rows,
            "stats": platform_stats(session, period),
            "period": period,
            "catalog": list(CATALOG.values()),
            "money": format_sar,
        })

    @router.post("/tenants/create")
    def create_tenant(
        request: Request,
        slug: str = Form(...),
        name: str = Form(...),
        owner_email: str = Form(...),
        owner_password: str = Form(...),
        plan: str = Form(default="trial"),
        rooms: int = Form(default=0),
        seed_kb: str = Form(default=""),
        session: Session = Depends(get_session),
        admin: PlatformAdmin | None = Depends(current_admin),
    ) -> Response:
        """تهيئة فندق جديد كاملة: الفندق، مالكه، وقاعدة معرفة مبدئية."""
        if admin is None:
            return _login_redirect()
        slug = slug.strip().lower()
        if tenant_by_slug(session, slug) or staff_by_email(session, owner_email):
            return RedirectResponse("/platform?err=duplicate", status_code=303)
        if len(owner_password) < 8:
            return RedirectResponse("/platform?err=weak", status_code=303)

        tenant = Tenant(
            slug=slug,
            name=name.strip(),
            plan=plan if plan in CATALOG else "trial",
            rooms=rooms,
            trial_ends_at=(now_riyadh() + timedelta(days=CATALOG["trial"].trial_days)).date()
            if plan == "trial"
            else None,
        )
        session.add(tenant)
        session.flush()

        session.add(
            StaffUser(
                tenant_id=tenant.id,
                email=owner_email.strip().lower(),
                name=name.strip(),
                password_hash=hash_password(owner_password),
                role="owner",
            )
        )
        if seed_kb:
            _seed_knowledge(session, tenant.id)
        audit(session, tenant.id, admin.email, "platform.tenant.create",
              entity="tenant", entity_id=slug)
        logger.info("أُنشئ فندق جديد: %s على باقة %s", slug, tenant.plan)
        return RedirectResponse("/platform?ok=1", status_code=303)

    @router.post("/tenants/{tenant_id}/update")
    def update_tenant(
        tenant_id: int,
        plan: str = Form(default=""),
        rooms: int = Form(default=-1),
        active: str = Form(default=""),
        session: Session = Depends(get_session),
        admin: PlatformAdmin | None = Depends(current_admin),
    ) -> Response:
        if admin is None:
            return _login_redirect()
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            return RedirectResponse("/platform", status_code=303)
        if plan in CATALOG:
            tenant.plan = plan
        if rooms >= 0:
            tenant.rooms = rooms
        tenant.active = bool(active)
        audit(session, tenant.id, admin.email, "platform.tenant.update",
              entity="tenant", entity_id=tenant.slug,
              detail=f"plan={tenant.plan} rooms={tenant.rooms} active={tenant.active}")
        return RedirectResponse("/platform?ok=1", status_code=303)

    @router.post("/invoices/run")
    def run_invoices(
        session: Session = Depends(get_session),
        admin: PlatformAdmin | None = Depends(current_admin),
    ) -> Response:
        """إصدار فواتير الشهر المنقضي لكل الفنادق النشطة."""
        if admin is None:
            return _login_redirect()
        period = billing.previous_period(billing.period_of())
        issued = 0
        for tenant in list_tenants(session, include_inactive=False):
            billing.issue_invoice(session, tenant, period)
            issued += 1
        logger.info("أُصدرت فواتير %s لـ%d فندقًا", period, issued)
        return RedirectResponse(f"/platform?ok=1&issued={issued}", status_code=303)

    return router


def _seed_knowledge(session: Session, tenant_id: int) -> None:
    """قاعدة معرفة مبدئية: فندق جديد يبدأ بحقائق يعدّلها لا بصفحة فارغة."""
    from pathlib import Path

    import yaml

    seed = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge_base.yaml"
    if not seed.exists():
        return
    records = yaml.safe_load(seed.read_text(encoding="utf-8")) or []
    KnowledgeBase.from_records(records)  # يُرفض الملف الفاسد قبل الكتابة
    for record in records:
        seasons = record.get("season") or ["normal", "ramadan", "hajj"]
        if isinstance(seasons, str):
            seasons = [seasons]
        session.add(
            Fact(
                tenant_id=tenant_id,
                key=str(record["id"]),
                text=str(record["text"]).strip(),
                topic=str(record.get("topic") or ""),
                seasons=",".join(seasons),
                hours=record.get("hours"),
                paid=bool(record.get("paid", False)),
                # الحقائق المبدئية معطّلة: المدير يراجعها قبل أن ينطق بها المساعد.
                active=False,
                updated_by="seed",
            )
        )
