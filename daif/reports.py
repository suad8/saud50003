"""تصدير البيانات وتقرير الوردية.

بيانات الفندق ملك الفندق: التصدير حق لا ميزة. وتقرير الوردية يجيب على سؤال
مناوب الليل الواحد: ماذا فاتني، وما الذي ما زال معلّقًا؟
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .clock import now_riyadh, to_riyadh
from .models import HandoffRecord, Invoice, Message, Ticket
from .plans import format_sar

# ترويسة BOM حتى يفتح إكسل العربية بترميز صحيح بدل الطلاسم
_BOM = "\ufeff"

# محارف تجعل إكسل يعامل الخلية كصيغة تُنفَّذ لا كنص يُعرض
_FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value) -> str:
    """يحصّن الخلية من حقن الصيغ.

    نص التحويلات يأتي من النزيل نفسه. لو بدأ بـ`=` فتحه إكسل كصيغة قابلة
    للتنفيذ — فيصير تصدير تقرير بريء بابًا لتنفيذ أمر على جهاز المدير.
    الفاصلة العليا تجعل إكسل يعرضه نصًا، وتختفي عند القراءة.
    """
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_STARTERS):
        return "'" + text
    return text


def _to_csv(header: list[str], rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([_safe_cell(cell) for cell in row] for row in rows)
    return _BOM + buffer.getvalue()


def tickets_csv(session: Session, tenant_id: int, *, days: int = 90) -> str:
    since = now_riyadh() - timedelta(days=days)
    rows = session.scalars(
        select(Ticket)
        .where(Ticket.tenant_id == tenant_id, Ticket.created_at >= since)
        .order_by(Ticket.created_at.desc())
    )
    return _to_csv(
        ["التاريخ", "الغرفة", "النوع", "التفاصيل", "الوقت المطلوب", "الأولوية", "الحالة", "المسؤول"],
        [
            [
                t.created_at.strftime("%Y-%m-%d %H:%M"),
                t.room,
                t.type,
                t.detail,
                t.requested_time.strftime("%Y-%m-%d %H:%M") if t.requested_time else "",
                t.urgency,
                t.status,
                t.assigned_to,
            ]
            for t in rows
        ],
    )


def handoffs_csv(session: Session, tenant_id: int, *, days: int = 90) -> str:
    since = now_riyadh() - timedelta(days=days)
    rows = session.scalars(
        select(HandoffRecord)
        .where(HandoffRecord.tenant_id == tenant_id, HandoffRecord.created_at >= since)
        .order_by(HandoffRecord.created_at.desc())
    )
    return _to_csv(
        ["التاريخ", "السبب", "إلى", "سؤال النزيل", "الملاحظة", "الحالة", "أُغلقت بواسطة"],
        [
            [
                h.created_at.strftime("%Y-%m-%d %H:%M"),
                h.reason,
                h.to,
                h.guest_text,
                h.note,
                h.status,
                h.resolved_by,
            ]
            for h in rows
        ],
    )


def invoices_csv(session: Session, tenant_id: int) -> str:
    rows = session.scalars(
        select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.period.desc())
    )
    return _to_csv(
        ["رقم الفاتورة", "الفترة", "الباقة", "الاشتراك", "التجاوز", "الوعاء", "الضريبة", "الإجمالي", "الحالة"],
        [
            [
                i.number,
                i.period,
                i.plan_code,
                format_sar(i.subscription_amount),
                format_sar(i.overage_amount),
                format_sar(i.subtotal),
                format_sar(i.vat_amount),
                format_sar(i.total),
                i.status,
            ]
            for i in rows
        ],
    )


@dataclass
class ShiftReport:
    """ملخّص وردية واحدة — ما جرى، وما بقي معلّقًا."""

    since: datetime
    until: datetime
    inbound: int = 0
    answered: int = 0
    handed_off: int = 0
    tickets_opened: int = 0
    tickets_open_now: int = 0
    handoffs_open_now: int = 0
    urgent_open: int = 0
    languages: list[tuple[str, int]] = field(default_factory=list)
    top_handoff_reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def automation_rate(self) -> int:
        total = self.answered + self.handed_off
        return round(self.answered / total * 100) if total else 0

    @property
    def needs_attention(self) -> bool:
        return bool(self.urgent_open or self.handoffs_open_now)


def shift_report(session: Session, tenant_id: int, *, hours: int = 12) -> ShiftReport:
    """تقرير آخر وردية."""
    until = now_riyadh()
    since = until - timedelta(hours=hours)
    window = (Message.tenant_id == tenant_id, Message.created_at >= since)

    report = ShiftReport(since=since, until=until)
    report.inbound = session.scalar(
        select(func.count(Message.id)).where(*window, Message.direction == "in")
    ) or 0
    report.answered = session.scalar(
        select(func.count(Message.id)).where(
            *window, Message.direction == "out", Message.in_scope.is_(True)
        )
    ) or 0
    report.handed_off = session.scalar(
        select(func.count(HandoffRecord.id)).where(
            HandoffRecord.tenant_id == tenant_id, HandoffRecord.created_at >= since
        )
    ) or 0
    report.tickets_opened = session.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.tenant_id == tenant_id, Ticket.created_at >= since
        )
    ) or 0
    report.tickets_open_now = session.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.tenant_id == tenant_id, Ticket.status.in_(("open", "in_progress"))
        )
    ) or 0
    report.urgent_open = session.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.tenant_id == tenant_id,
            Ticket.status.in_(("open", "in_progress")),
            Ticket.urgency == "urgent",
        )
    ) or 0
    report.handoffs_open_now = session.scalar(
        select(func.count(HandoffRecord.id)).where(
            HandoffRecord.tenant_id == tenant_id, HandoffRecord.status == "open"
        )
    ) or 0
    report.languages = [
        (row[0], row[1])
        for row in session.execute(
            select(Message.language, func.count(Message.id))
            .where(*window, Message.direction == "out", Message.language != "")
            .group_by(Message.language)
            .order_by(func.count(Message.id).desc())
        ).all()
    ]
    report.top_handoff_reasons = [
        (row[0], row[1])
        for row in session.execute(
            select(HandoffRecord.reason, func.count(HandoffRecord.id))
            .where(HandoffRecord.tenant_id == tenant_id, HandoffRecord.created_at >= since)
            .group_by(HandoffRecord.reason)
            .order_by(func.count(HandoffRecord.id).desc())
        ).all()
    ]
    return report


def shift_report_text(report: ShiftReport, hotel_name: str) -> str:
    """نسخة نصية قصيرة تصلح لإرسالها لمدير الوردية."""
    lines = [
        f"تقرير وردية — {hotel_name}",
        f"من {to_riyadh(report.since).strftime('%m-%d %H:%M')} "
        f"إلى {to_riyadh(report.until).strftime('%m-%d %H:%M')}",
        "",
        f"رسائل واردة: {report.inbound}",
        f"أجاب المساعد: {report.answered} ({report.automation_rate}%)",
        f"حُوّل لموظف: {report.handed_off}",
        f"تذاكر فُتحت: {report.tickets_opened}",
        "",
        f"معلّق الآن — تذاكر: {report.tickets_open_now}"
        + (f" (منها {report.urgent_open} عاجلة)" if report.urgent_open else ""),
        f"معلّق الآن — تحويلات: {report.handoffs_open_now}",
    ]
    if report.languages:
        langs = "، ".join(f"{code}:{count}" for code, count in report.languages[:5])
        lines += ["", f"لغات النزلاء: {langs}"]
    return "\n".join(lines)
