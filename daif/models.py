"""نماذج قاعدة البيانات — منصة متعددة الفنادق.

قاعدة العزل: كل جدول يخص فندقًا يحمل `tenant_id`، وكل استعلام في
`repository.py` يشترط هذا الحقل. لا يوجد مسار قراءة واحد بلا تحديد الفندق.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .clock import now_riyadh


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return now_riyadh()


class Tenant(Base):
    """فندق مشترك في المنصة."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(64), default="المدينة المنورة")

    # --- تشغيل ---
    season: Mapped[str] = mapped_column(String(16), default="normal")
    hk_window: Mapped[str] = mapped_column(String(32), default="08:00-16:00")
    desk_status: Mapped[str] = mapped_column(String(16), default="staffed")

    # --- واتساب ---
    wa_phone_number_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    wa_access_token: Mapped[str] = mapped_column(Text, default="")

    # --- اشتراك ---
    plan: Mapped[str] = mapped_column(String(32), default="basic")
    rooms: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    facts: Mapped[list["Fact"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Fact(Base):
    """حقيقة موثّقة في قاعدة معرفة فندق."""

    __tablename__ = "facts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_fact_tenant_key"),
        Index("ix_fact_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(16))  # K01, K02…
    text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(64), default="")
    seasons: Mapped[str] = mapped_column(String(64), default="normal,ramadan,hajj")
    hours: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str] = mapped_column(String(120), default="")

    tenant: Mapped[Tenant] = relationship(back_populates="facts")

    def to_record(self) -> dict:
        """يحوّلها إلى الصيغة التي تفهمها `knowledge.KnowledgeBase`."""
        return {
            "id": self.key,
            "text": self.text,
            "topic": self.topic,
            "season": [s for s in (self.seasons or "").split(",") if s],
            "hours": self.hours,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "paid": self.paid,
        }


class StaffUser(Base):
    """موظف فندق له حق الدخول للوحة."""

    __tablename__ = "staff_users"
    __table_args__ = (UniqueConstraint("email", name="uq_staff_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="staff")  # owner|manager|staff
    locale: Mapped[str] = mapped_column(String(8), default="ar")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Guest(Base):
    """نزيل معروف برقم واتساب، مربوط بغرفة موثّقة."""

    __tablename__ = "guests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "wa_id", name="uq_guest_tenant_wa"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    wa_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    # الغرفة الموثّقة. الفراغ يعني رقم غير مربوط — لا تُفتح له تذاكر.
    room: Mapped[str] = mapped_column(String(16), default="")
    group_mode: Mapped[str] = mapped_column(String(16), default="individual")
    language: Mapped[str] = mapped_column(String(8), default="")
    checkout_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def room_verified(self) -> bool:
        return bool((self.room or "").strip())


class Message(Base):
    """رسالة واحدة، واردة أو صادرة، مع كل ما نعرفه عن معالجتها."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_msg_tenant_time", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    guest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True, index=True)

    direction: Mapped[str] = mapped_column(String(8))  # in | out
    text: Mapped[str] = mapped_column(Text, default="")
    wa_message_id: Mapped[str] = mapped_column(String(128), default="", index=True)

    # --- ناتج المعالجة (للرسائل الصادرة) ---
    language: Mapped[str] = mapped_column(String(8), default="")
    intent: Mapped[str] = mapped_column(String(24), default="")
    in_scope: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    sources: Mapped[str] = mapped_column(String(255), default="")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    violations: Mapped[str] = mapped_column(Text, default="")
    restricted_category: Mapped[str] = mapped_column(String(32), default="")
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- تكلفة وأداء ---
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Ticket(Base):
    """تذكرة خدمة لفريق العمليات."""

    __tablename__ = "tickets"
    __table_args__ = (Index("ix_ticket_tenant_status", "tenant_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    guest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)

    type: Mapped[str] = mapped_column(String(24))
    room: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text)
    requested_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    urgency: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|in_progress|done|cancelled
    assigned_to: Mapped[str] = mapped_column(String(120), default="")
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class HandoffRecord(Base):
    """تحويل إلى موظف بشري — كل واحد منها سؤال لم يستطع المساعد تغطيته."""

    __tablename__ = "handoffs"
    __table_args__ = (Index("ix_handoff_tenant_status", "tenant_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    guest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)

    reason: Mapped[str] = mapped_column(String(32), index=True)
    to: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")
    # نص سؤال النزيل — أساس لوحة «الفجوات» التي تقترح حقائق جديدة
    guest_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved
    resolved_by: Mapped[str] = mapped_column(String(120), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """سجل تدقيق لكل تعديل على قاعدة المعرفة أو الإعدادات."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
