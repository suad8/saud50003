"""قاعدة معرفة الفندق: المصدر الوحيد للحقيقة.

كل حقيقة تحمل معرّفًا ثابتًا (K01…) يجب أن يستشهد به الجواب. الحقائق تُفلتر
بالموسم وبصلاحية التاريخ قبل أن يراها النموذج، وتُرفق كل حقيقة ذات نافذة تشغيل
بحالة «مفتوح/مغلق الآن» محسوبة بالكود — حتى لا يضطر النموذج لحساب الوقت بنفسه.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .clock import TimeWindow, parse_date, parse_window, to_riyadh

SEASONS = ("normal", "ramadan", "hajj")
ALL_SEASONS = frozenset(SEASONS)


class KnowledgeError(ValueError):
    """قاعدة معرفة غير صالحة — تُرفض عند التحميل لا عند أول نزيل."""


@dataclass(frozen=True)
class Fact:
    """حقيقة واحدة موثّقة عن الفندق."""

    id: str
    text: str
    topic: str = ""
    seasons: frozenset[str] = field(default=ALL_SEASONS)
    hours: TimeWindow | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    paid: bool = False

    def is_active(self, moment: datetime, season: str) -> bool:
        """هل هذه الحقيقة صالحة للعرض في هذا الموسم وهذا التاريخ؟"""
        if season not in self.seasons:
            return False
        today = to_riyadh(moment).date()
        start = parse_date(self.valid_from)
        end = parse_date(self.valid_until)
        if start and today < start:
            return False
        if end and today > end:
            return False
        return True

    def status_hint(self, moment: datetime) -> str:
        """حالة الخدمة الآن، محسوبة لا مستنتجة. فارغة إن لم يكن للحقيقة وقت تشغيل."""
        if self.hours is None:
            return ""
        local = to_riyadh(moment)
        if self.hours.contains(local):
            closing = self.hours.closing_after(local)
            close_txt = closing.strftime("%H:%M") if closing else str(self.hours.end)
            return f"وقت الخدمة {self.hours} — مفتوح الآن، يغلق {close_txt}"
        opening = self.hours.next_opening(local)
        when = "اليوم" if opening.date() == local.date() else "غدًا"
        return f"وقت الخدمة {self.hours} — مغلق الآن، يفتح {opening.strftime('%H:%M')} {when}"

    def render(self, moment: datetime) -> str:
        """سطر الحقيقة كما يراه النموذج."""
        line = f"{self.id} | {self.text}"
        marks = []
        hint = self.status_hint(moment)
        if hint:
            marks.append(hint)
        if self.valid_until:
            marks.append(f"ينتهي العمل بها {self.valid_until}")
        if self.paid:
            marks.append("خدمة مدفوعة")
        if marks:
            line += "  ⟦" + " · ".join(marks) + "⟧"
        return line


def _coerce_seasons(raw: object, fact_id: str) -> frozenset[str]:
    if raw is None:
        return ALL_SEASONS
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        values = [str(v) for v in raw]
    else:
        raise KnowledgeError(f"{fact_id}: قيمة season غير مفهومة")
    unknown = [v for v in values if v not in SEASONS]
    if unknown:
        raise KnowledgeError(f"{fact_id}: موسم غير معروف {unknown!r}")
    if not values:
        return ALL_SEASONS
    return frozenset(values)


def fact_from_record(record: dict) -> Fact:
    """يبني حقيقة من قاموس (من YAML أو من قاعدة البيانات)."""
    fact_id = str(record.get("id") or "").strip()
    if not fact_id:
        raise KnowledgeError("حقيقة بلا معرّف")
    text = str(record.get("text") or "").strip()
    if not text:
        raise KnowledgeError(f"{fact_id}: حقيقة بلا نص")

    hours_raw = record.get("hours")
    hours = parse_window(hours_raw) if hours_raw else None
    if hours_raw and hours is None:
        raise KnowledgeError(f"{fact_id}: صيغة وقت غير صالحة {hours_raw!r}")

    for key in ("valid_from", "valid_until"):
        value = record.get(key)
        if value and parse_date(str(value)) is None:
            raise KnowledgeError(f"{fact_id}: تاريخ غير صالح في {key}: {value!r}")

    return Fact(
        id=fact_id,
        text=text,
        topic=str(record.get("topic") or "").strip(),
        seasons=_coerce_seasons(record.get("season"), fact_id),
        hours=hours,
        valid_from=str(record["valid_from"]) if record.get("valid_from") else None,
        valid_until=str(record["valid_until"]) if record.get("valid_until") else None,
        paid=bool(record.get("paid", False)),
    )


@dataclass(frozen=True)
class KnowledgeBase:
    """مجموعة حقائق فندق واحد."""

    facts: tuple[Fact, ...] = ()

    @classmethod
    def from_records(cls, records: list[dict]) -> KnowledgeBase:
        facts = [fact_from_record(r) for r in records]
        seen: set[str] = set()
        for fact in facts:
            if fact.id in seen:
                raise KnowledgeError(f"معرّف مكرر: {fact.id}")
            seen.add(fact.id)
        return cls(facts=tuple(facts))

    @property
    def ids(self) -> frozenset[str]:
        """كل المعرّفات الموجودة فعلًا — تُستخدم لكشف المصادر المخترعة."""
        return frozenset(f.id for f in self.facts)

    def get(self, fact_id: str) -> Fact | None:
        for fact in self.facts:
            if fact.id == fact_id:
                return fact
        return None

    def active(self, moment: datetime, season: str) -> tuple[Fact, ...]:
        """الحقائق الصالحة الآن لهذا الموسم."""
        return tuple(f for f in self.facts if f.is_active(moment, season))

    def active_ids(self, moment: datetime, season: str) -> frozenset[str]:
        return frozenset(f.id for f in self.active(moment, season))

    def render(self, moment: datetime, season: str) -> str:
        """كتلة الحقائق التي تُحقن في البرومبت."""
        active = self.active(moment, season)
        if not active:
            return "(لا توجد حقائق موثّقة صالحة الآن)"
        return "\n".join(f.render(moment) for f in active)
