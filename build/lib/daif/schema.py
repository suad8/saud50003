"""عقد المخرجات. النموذج ملزَم بهذه الصيغة عبر Structured Outputs.

ملاحظة: الحقول هنا بأنواع بسيطة بلا قيود مدى أو أنماط، لأن مخطط JSON الصارم
لا يضمن دعم كل قيود Pydantic. التحقق من المدى والمنطق يجري في `guardrails.py`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Intent = Literal["inquiry", "request", "complaint", "out_of_scope"]

RequestType = Literal[
    "cleaning",
    "towels",
    "amenities",
    "maintenance",
    "laundry",
    "late_checkout",
    "luggage",
    "wake_up",
    "transport",
    "other",
]

Urgency = Literal["normal", "urgent"]

HandoffReason = Literal[
    "no_documented_answer",
    "restricted_topic",
    "complaint",
    "unverified_room",
    "low_confidence",
    "out_of_hours",
    "time_uncertain",
]

HandoffTo = Literal["front_desk", "duty_manager", "housekeeping"]

# طلبات يحكمها جدول التدبير الفندقي — الوقت المطلوب فيها يجب أن يقع داخل النافذة
HOUSEKEEPING_TYPES: frozenset[str] = frozenset(
    {"cleaning", "towels", "amenities", "laundry"}
)


class ServiceRequest(BaseModel):
    """تذكرة خدمة يفتحها المساعد لفريق العمليات.

    `rooms` توسعة لوضع المجموعات: المواصفة تقبل طلبًا يغطي عدة غرف إذا ذكرها
    المطوّف صراحة، لكن العقد الأساسي يحمل غرفة واحدة. الحقل اختياري وفارغ في
    الوضع الفردي، فلا يغيّر سلوك العقد الأصلي. كل غرفة فيه تُطابَق مع قائمة
    الغرف المصرّح بها للمطوّف — القائمة من سجلات الفندق لا من نص الرسالة.
    """

    model_config = ConfigDict(extra="forbid")

    type: RequestType
    room: str
    detail: str
    requested_time: str | None = None
    urgency: Urgency = "normal"
    rooms: list[str] = []

    @property
    def all_rooms(self) -> list[str]:
        """كل الغرف المشمولة، بلا تكرار وبترتيب ثابت."""
        seen: list[str] = []
        for candidate in [self.room, *self.rooms]:
            value = (candidate or "").strip()
            if value and value not in seen:
                seen.append(value)
        return seen


class Handoff(BaseModel):
    """تحويل إلى موظف بشري."""

    model_config = ConfigDict(extra="forbid")

    reason: HandoffReason
    to: HandoffTo
    note: str


class GuestReply(BaseModel):
    """الرد الكامل على رسالة نزيل واحدة."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    in_scope: bool
    language: str
    answer: str
    sources: list[str]
    request: ServiceRequest | None = None
    handoff: Handoff | None = None
    confidence: float


def safe_handoff(
    reason: HandoffReason,
    to: HandoffTo = "front_desk",
    note: str = "",
    answer: str = "سيتواصل معك الاستقبال.",
    language: str = "ar",
    intent: Intent = "out_of_scope",
) -> GuestReply:
    """رد التحويل الافتراضي — يُستعمل كلما فشل أي شيء آخر."""
    return GuestReply(
        intent=intent,
        in_scope=False,
        language=language,
        answer=answer,
        sources=[],
        request=None,
        handoff=Handoff(reason=reason, to=to, note=note or "يحتاج تدخل موظف"),
        confidence=1.0,
    )
