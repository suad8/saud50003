"""أدوات الوقت. كل ما يخص العمليات يجري بتوقيت الرياض."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")

# يقبل 08:00-16:00 و 08:00–16:00 (شرطة طويلة) و 8:00 — 16:00
_WINDOW_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$")


def now_riyadh() -> datetime:
    """اللحظة الحالية بتوقيت الرياض."""
    return datetime.now(RIYADH)


def to_riyadh(moment: datetime) -> datetime:
    """يحوّل أي لحظة إلى توقيت الرياض. اللحظة بلا منطقة تُعتبر رياضية أصلًا."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=RIYADH)
    return moment.astimezone(RIYADH)


@dataclass(frozen=True)
class TimeWindow:
    """نافذة تشغيل يومية. تدعم النوافذ التي تعبر منتصف الليل (٢٢:٠٠–٠٢:٠٠)."""

    start: time
    end: time

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start

    def contains(self, moment: datetime) -> bool:
        """هل اللحظة داخل النافذة؟"""
        current = to_riyadh(moment).time()
        if not self.crosses_midnight:
            return self.start <= current < self.end
        return current >= self.start or current < self.end

    def next_opening(self, moment: datetime) -> datetime:
        """أقرب لحظة تفتح فيها النافذة ابتداءً من `moment`."""
        local = to_riyadh(moment)
        today_start = datetime.combine(local.date(), self.start, tzinfo=RIYADH)
        if self.contains(local):
            return local
        if local < today_start:
            return today_start
        return today_start + timedelta(days=1)

    def closing_after(self, moment: datetime) -> datetime | None:
        """لحظة الإغلاق المقابلة إن كنا داخل النافذة الآن، وإلا None."""
        local = to_riyadh(moment)
        if not self.contains(local):
            return None
        end_today = datetime.combine(local.date(), self.end, tzinfo=RIYADH)
        if self.crosses_midnight and local.time() >= self.start:
            return end_today + timedelta(days=1)
        return end_today

    def __str__(self) -> str:
        return f"{self.start.strftime('%H:%M')}–{self.end.strftime('%H:%M')}"


def parse_window(raw: str | None) -> TimeWindow | None:
    """يحلّل نصًا مثل «08:00-16:00». يعيد None إن كان النص غير صالح."""
    if not raw:
        return None
    match = _WINDOW_RE.match(raw)
    if not match:
        return None
    sh, sm, eh, em = (int(g) for g in match.groups())
    if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
        return None
    return TimeWindow(start=time(sh, sm), end=time(eh, em))


def parse_date(raw: str | None) -> date | None:
    """يحلّل تاريخًا بصيغة YYYY-MM-DD."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_iso8601(raw: str | None) -> datetime | None:
    """يحلّل طابعًا زمنيًا ISO 8601 ويعيده بتوقيت الرياض."""
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        return to_riyadh(datetime.fromisoformat(text))
    except ValueError:
        return None


def format_now(moment: datetime) -> str:
    """صيغة الوقت التي تُحقن في البرومبت — واضحة للنموذج وبلا لبس."""
    local = to_riyadh(moment)
    days = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    return f"{local.strftime('%Y-%m-%d %H:%M')} ({days[local.weekday()]}، +03)"
