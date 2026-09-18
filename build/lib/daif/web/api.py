"""واجهة برمجية لأنظمة إدارة الفنادق (PMS).

المشكلة اللي تحلّها: ربط رقم واتساب النزيل بغرفته. بدون تكامل، موظف
الاستقبال يدخله يدويًا لكل نزيل — وهذا أكبر احتكاك في التشغيل، وأكثر سبب
لموت التجارب. مع تكامل مثل جرس أو فندقة، الغرفة تجي مع تسجيل الدخول
وتنفكّ مع المغادرة، بلا لمسة بشرية.

مبدأ أمني: الفندق يُشتقّ من المفتاح دائمًا، ولا يُقبَل أبدًا من متن الطلب.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from .. import apikeys
from ..clock import now_riyadh
from ..db import get_session
from ..models import Guest, Tenant, Ticket
from ..ratelimit import Limit, limiter
from ..repository import get_or_create_guest, list_tickets

logger = logging.getLogger("daif.api")

router = APIRouter(prefix="/api/v1", tags=["pms"])

# سخيّ لتزامن تسجيل وصول مجموعة كاملة، ومحدود مع ذلك.
API_LIMIT = Limit(attempts=300, window=60)

TICKET_STATUSES = ("open", "in_progress", "done", "cancelled")


# ---------------------------------------------------------------------------
# المصادقة
# ---------------------------------------------------------------------------

def authenticated_tenant(
    request: Request,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Tenant:
    client_ip = request.client.host if request.client else "?"
    if not limiter.hit(f"api:{client_ip}", API_LIMIT):
        raise HTTPException(status_code=429, detail="rate limited")

    tenant = apikeys.authenticate(session, apikeys.bearer_token(authorization))
    if tenant is None:
        logger.warning("محاولة وصول بمفتاح غير صالح من %s", client_ip)
        raise HTTPException(
            status_code=401,
            detail="مفتاح غير صالح",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return tenant


# ---------------------------------------------------------------------------
# الأشكال
# ---------------------------------------------------------------------------

class CheckIn(BaseModel):
    """تسجيل وصول: يربط رقم واتساب بغرفة موثّقة."""

    model_config = ConfigDict(extra="forbid")

    wa_id: str = Field(min_length=6, max_length=32, description="رقم واتساب بصيغة دولية بلا +")
    room: str = Field(min_length=1, max_length=16)
    name: str = Field(default="", max_length=120)
    language: str = Field(default="", max_length=8)
    group_mode: str = Field(default="individual")
    group_rooms: list[str] = Field(default_factory=list)
    checkout_on: date | None = None

    @field_validator("wa_id")
    @classmethod
    def digits_only(cls, value: str) -> str:
        cleaned = value.strip().lstrip("+").replace(" ", "").replace("-", "")
        if not cleaned.isdigit():
            raise ValueError("رقم واتساب يجب أن يكون أرقامًا فقط")
        return cleaned

    @field_validator("group_mode")
    @classmethod
    def known_mode(cls, value: str) -> str:
        if value not in ("individual", "group_leader"):
            raise ValueError("وضع غير معروف")
        return value


class CheckOut(BaseModel):
    """مغادرة: تفكّ الربط فورًا فلا تُفتح تذاكر باسم غرفة سلّمها صاحبها."""

    model_config = ConfigDict(extra="forbid")

    wa_id: str = Field(min_length=6, max_length=32)

    @field_validator("wa_id")
    @classmethod
    def digits_only(cls, value: str) -> str:
        cleaned = value.strip().lstrip("+").replace(" ", "").replace("-", "")
        if not cleaned.isdigit():
            raise ValueError("رقم واتساب يجب أن يكون أرقامًا فقط")
        return cleaned


class TicketStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in TICKET_STATUSES:
            raise ValueError(f"حالة غير معروفة، المسموح: {', '.join(TICKET_STATUSES)}")
        return value


def _guest_json(guest: Guest) -> dict:
    return {
        "wa_id": guest.wa_id,
        "room": guest.room,
        "name": guest.name,
        "group_mode": guest.group_mode,
        "group_rooms": list(guest.room_list),
        "checkout_on": guest.checkout_on.isoformat() if guest.checkout_on else None,
        "room_verified": guest.room_verified,
    }


def _ticket_json(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "type": ticket.type,
        "room": ticket.room,
        "detail": ticket.detail,
        "requested_time": ticket.requested_time.isoformat() if ticket.requested_time else None,
        "urgency": ticket.urgency,
        "status": ticket.status,
        "escalated": ticket.escalated,
        "created_at": ticket.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# المسارات
# ---------------------------------------------------------------------------

@router.get("/ping")
def ping(tenant: Tenant = Depends(authenticated_tenant)) -> dict:
    """تحقّق سريع أن المفتاح شغّال ومربوط بالفندق الصحيح."""
    return {"ok": True, "hotel": tenant.name, "slug": tenant.slug}


@router.post("/guests/check-in", status_code=200)
def check_in(
    payload: CheckIn,
    tenant: Tenant = Depends(authenticated_tenant),
    session: Session = Depends(get_session),
) -> dict:
    """يربط رقم النزيل بغرفته. يُنادى من الـPMS عند تسجيل الوصول."""
    guest = get_or_create_guest(session, tenant.id, payload.wa_id)
    guest.room = payload.room.strip()
    guest.name = payload.name.strip() or guest.name
    guest.language = payload.language.strip() or guest.language
    guest.group_mode = payload.group_mode
    # قائمة الغرف بلا معنى خارج وضع المطوّف — تُفرَّغ حتى لا تمنح صلاحية صامتة
    guest.group_rooms = (
        ",".join(r.strip() for r in payload.group_rooms if r.strip())
        if payload.group_mode == "group_leader"
        else ""
    )
    guest.checkout_on = payload.checkout_on
    session.flush()
    logger.info("تسجيل وصول عبر الواجهة: %s غرفة %s", tenant.slug, guest.room)
    return _guest_json(guest)


@router.post("/guests/check-out", status_code=200)
def check_out(
    payload: CheckOut,
    tenant: Tenant = Depends(authenticated_tenant),
    session: Session = Depends(get_session),
) -> dict:
    """يفكّ ربط الغرفة. بعدها لا يفتح المساعد أي تذكرة لهذا الرقم."""
    guest = session.query(Guest).filter(
        Guest.tenant_id == tenant.id, Guest.wa_id == payload.wa_id
    ).one_or_none()
    if guest is None:
        raise HTTPException(status_code=404, detail="نزيل غير معروف")
    guest.room = ""
    guest.group_rooms = ""
    guest.group_mode = "individual"
    guest.checkout_on = now_riyadh().date()
    session.flush()
    logger.info("مغادرة عبر الواجهة: %s", tenant.slug)
    return _guest_json(guest)


@router.get("/guests/{wa_id}")
def get_guest(
    wa_id: str,
    tenant: Tenant = Depends(authenticated_tenant),
    session: Session = Depends(get_session),
) -> dict:
    guest = session.query(Guest).filter(
        Guest.tenant_id == tenant.id, Guest.wa_id == wa_id
    ).one_or_none()
    if guest is None:
        raise HTTPException(status_code=404, detail="نزيل غير معروف")
    return _guest_json(guest)


@router.get("/tickets")
def tickets(
    status: str = "open",
    limit: int = 100,
    tenant: Tenant = Depends(authenticated_tenant),
    session: Session = Depends(get_session),
) -> dict:
    """يسحب التذاكر ليعرضها الـPMS في شاشة التدبير الفندقي."""
    if status not in (*TICKET_STATUSES, "all"):
        raise HTTPException(status_code=422, detail="حالة غير معروفة")
    rows = list_tickets(
        session, tenant.id, status=None if status == "all" else status, limit=min(limit, 500)
    )
    return {"count": len(rows), "tickets": [_ticket_json(t) for t in rows]}


@router.post("/tickets/{ticket_id}/status")
def set_ticket_status(
    ticket_id: int,
    payload: TicketStatus,
    tenant: Tenant = Depends(authenticated_tenant),
    session: Session = Depends(get_session),
) -> dict:
    """يغلق التذكرة من الـPMS، فلا يحتاج العامل لوحتين."""
    ticket = session.get(Ticket, ticket_id)
    # حاجز العزل: تذكرة فندق آخر لا توجد بالنسبة لهذا المفتاح
    if ticket is None or ticket.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="تذكرة غير معروفة")
    ticket.status = payload.status
    ticket.assigned_to = "pms"
    ticket.closed_at = now_riyadh() if payload.status in ("done", "cancelled") else None
    session.flush()
    return _ticket_json(ticket)
