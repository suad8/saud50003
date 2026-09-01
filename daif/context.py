"""سياق التشغيل لرسالة واحدة: أي فندق، أي نزيل، أي لحظة، أي موسم."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clock import TimeWindow, format_now, parse_window

Season = str  # normal | ramadan | hajj
DeskStatus = str  # staffed | thin | unstaffed
GroupMode = str  # individual | group_leader

VALID_SEASONS = ("normal", "ramadan", "hajj")
VALID_DESK = ("staffed", "thin", "unstaffed")
VALID_GROUP = ("individual", "group_leader")


@dataclass(frozen=True)
class GuestContext:
    """كل ما يعرفه النظام عن الموقف قبل استدعاء النموذج."""

    hotel_name: str
    now: datetime
    season: Season = "normal"
    room: str = ""
    guest_name: str = ""
    hk_window_raw: str = "08:00-16:00"
    desk_status: DeskStatus = "staffed"
    group_mode: GroupMode = "individual"
    # غرف المطوّف، من سجلات الفندق فقط. فارغة في الوضع الفردي.
    group_rooms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.season not in VALID_SEASONS:
            raise ValueError(f"موسم غير معروف: {self.season}")
        if self.desk_status not in VALID_DESK:
            raise ValueError(f"حالة استقبال غير معروفة: {self.desk_status}")
        if self.group_mode not in VALID_GROUP:
            raise ValueError(f"وضع مجموعة غير معروف: {self.group_mode}")

    @property
    def hk_window(self) -> TimeWindow | None:
        """نافذة التدبير الفندقي بعد التحليل. None إن كانت الصيغة غير صالحة."""
        return parse_window(self.hk_window_raw)

    @property
    def room_verified(self) -> bool:
        """هل الرقم مربوط بغرفة موثّقة؟ الفراغ يعني لا."""
        return bool(self.room.strip()) or bool(self.authorised_rooms)

    @property
    def is_group_leader(self) -> bool:
        return self.group_mode == "group_leader"

    @property
    def authorised_rooms(self) -> tuple[str, ...]:
        """الغرف التي يجوز فتح تذكرة لها في هذه المحادثة.

        في الوضع الفردي: غرفة النزيل وحدها.
        في وضع المطوّف: غرفته وغرف مجموعته المسجّلة في الفندق.
        """
        rooms: list[str] = []
        own = self.room.strip()
        if own:
            rooms.append(own)
        if self.is_group_leader:
            for entry in self.group_rooms:
                value = entry.strip()
                if value and value not in rooms:
                    rooms.append(value)
        return tuple(rooms)

    @property
    def now_text(self) -> str:
        return format_now(self.now)
