"""قياس الاستخدام وإصدار الفواتير.

قرار منتج: **تجاوز الحصة يُفوتر ولا يُسقط النزلاء.** إيقاف المساعد في ذروة
الموسم لأن الفندق تجاوز عدّاده يعني حاجًا مسنًا بلا جواب — وهو ضرر أكبر من
أي تأخّر في التحصيل. الزيادة تُحتسب بسعر معلن، ويُنبَّه المدير في اللوحة.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import now_riyadh
from .models import Invoice, Tenant, UsageCounter
from .plans import VAT_RATE_BP, Plan, format_sar, get as get_plan


def period_of(moment: datetime | date | None = None) -> str:
    """معرّف الفترة الشهرية: 2026-09."""
    if moment is None:
        moment = now_riyadh()
    if isinstance(moment, datetime):
        moment = moment.date()
    return f"{moment.year:04d}-{moment.month:02d}"


def previous_period(period: str) -> str:
    year, month = (int(part) for part in period.split("-"))
    first = date(year, month, 1)
    return period_of(first - timedelta(days=1))


# ---------------------------------------------------------------------------
# قياس الاستخدام
# ---------------------------------------------------------------------------

def counter_for(session: Session, tenant_id: int, period: str | None = None) -> UsageCounter:
    """عدّاد الفترة، يُنشأ عند أول استعمال."""
    key = period or period_of()
    counter = session.scalar(
        select(UsageCounter).where(
            UsageCounter.tenant_id == tenant_id, UsageCounter.period == key
        )
    )
    if counter is None:
        counter = UsageCounter(tenant_id=tenant_id, period=key)
        session.add(counter)
        session.flush()
    return counter


def record(
    session: Session,
    tenant_id: int,
    *,
    inbound: int = 0,
    outbound: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tickets: int = 0,
    handoffs: int = 0,
    period: str | None = None,
) -> UsageCounter:
    """يسجّل استهلاك رسالة واحدة على عدّاد الشهر."""
    counter = counter_for(session, tenant_id, period)
    counter.inbound += inbound
    counter.outbound += outbound
    counter.tokens_in += tokens_in
    counter.tokens_out += tokens_out
    counter.tickets += tickets
    counter.handoffs += handoffs
    return counter


@dataclass(frozen=True)
class Quota:
    """حالة الحصة الشهرية كما تُعرض في اللوحة."""

    plan: Plan
    used: int
    included: int
    overage_messages: int
    overage_amount: int  # هللات

    @property
    def percent(self) -> int:
        if self.included <= 0:
            return 100
        return min(999, round(self.used / self.included * 100))

    @property
    def exceeded(self) -> bool:
        return self.overage_messages > 0

    @property
    def near_limit(self) -> bool:
        return not self.exceeded and self.percent >= 80


def quota(session: Session, tenant: Tenant, period: str | None = None) -> Quota:
    """يحسب حالة الحصة دون أن يمنع أي رسالة."""
    plan = get_plan(tenant.plan)
    counter = counter_for(session, tenant.id, period)
    over = max(0, counter.outbound - plan.included_messages)
    return Quota(
        plan=plan,
        used=counter.outbound,
        included=plan.included_messages,
        overage_messages=over,
        overage_amount=over * plan.overage,
    )


def rooms_exceeded(tenant: Tenant) -> bool:
    """هل تجاوز الفندق حدّ الغرف في باقته؟"""
    plan = get_plan(tenant.plan)
    return not plan.unlimited_rooms and tenant.rooms > plan.max_rooms


# ---------------------------------------------------------------------------
# ضريبة القيمة المضافة ورمز ZATCA
# ---------------------------------------------------------------------------

def vat_of(subtotal: int) -> int:
    """ضريبة القيمة المضافة بالهللات، بتقريب نصفي على أعداد صحيحة."""
    return (subtotal * VAT_RATE_BP + 5_000) // 10_000


def _tlv(tag: int, value: str) -> bytes:
    payload = value.encode("utf-8")
    return bytes([tag, len(payload)]) + payload


def zatca_qr(
    seller_name: str, vat_number: str, issued_at: datetime, total: int, vat: int
) -> str:
    """رمز الاستجابة السريعة للفاتورة المبسّطة بصيغة TLV المشفّرة بـ base64.

    الوسوم الخمسة الإلزامية: اسم البائع، رقمه الضريبي، الطابع الزمني،
    الإجمالي شاملًا الضريبة، ثم مبلغ الضريبة.
    """
    payload = (
        _tlv(1, seller_name)
        + _tlv(2, vat_number)
        + _tlv(3, issued_at.isoformat(timespec="seconds"))
        + _tlv(4, format_sar(total).replace(",", ""))
        + _tlv(5, format_sar(vat).replace(",", ""))
    )
    return base64.b64encode(payload).decode("ascii")


# ---------------------------------------------------------------------------
# إصدار الفواتير
# ---------------------------------------------------------------------------

def invoice_number(tenant_id: int, period: str) -> str:
    return f"DAIF-{period.replace('-', '')}-{tenant_id:05d}"


def issue_invoice(
    session: Session,
    tenant: Tenant,
    period: str,
    *,
    seller_name: str = "ضيف لتقنية الضيافة",
    seller_vat: str = "300000000000003",
    due_days: int = 14,
) -> Invoice:
    """يصدر فاتورة الفترة، أو يعيد الصادرة سلفًا — لا تُصدر فاتورتان لشهر."""
    existing = session.scalar(
        select(Invoice).where(Invoice.tenant_id == tenant.id, Invoice.period == period)
    )
    if existing is not None:
        return existing

    plan = get_plan(tenant.plan)
    state = quota(session, tenant, period)
    subscription = plan.monthly
    overage = state.overage_amount
    subtotal = subscription + overage
    vat = vat_of(subtotal)
    total = subtotal + vat
    issued = now_riyadh()

    invoice = Invoice(
        tenant_id=tenant.id,
        number=invoice_number(tenant.id, period),
        period=period,
        plan_code=plan.code,
        subscription_amount=subscription,
        overage_messages=state.overage_messages,
        overage_amount=overage,
        subtotal=subtotal,
        vat_amount=vat,
        total=total,
        status="paid" if total == 0 else "issued",
        issued_at=issued,
        due_at=issued + timedelta(days=due_days),
        seller_name=seller_name,
        seller_vat=seller_vat,
        zatca_qr=zatca_qr(seller_name, seller_vat, issued, total, vat),
    )
    session.add(invoice)
    session.flush()
    return invoice


def list_invoices(session: Session, tenant_id: int, limit: int = 24) -> list[Invoice]:
    return list(
        session.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id)
            .order_by(Invoice.period.desc())
            .limit(limit)
        )
    )
