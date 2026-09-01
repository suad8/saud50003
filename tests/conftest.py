"""تجهيزات مشتركة للاختبارات."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from daif.clock import RIYADH  # noqa: E402
from daif.context import GuestContext  # noqa: E402
from daif.knowledge import KnowledgeBase  # noqa: E402


@pytest.fixture
def kb() -> KnowledgeBase:
    records = yaml.safe_load((ROOT / "data" / "knowledge_base.yaml").read_text("utf-8"))
    return KnowledgeBase.from_records(records)


@pytest.fixture
def now() -> datetime:
    """الثلاثاء ١ سبتمبر ٢٠٢٦، ١١:١٥ صباحًا بتوقيت الرياض."""
    return datetime(2026, 9, 1, 11, 15, tzinfo=RIYADH)


@pytest.fixture
def ctx(now: datetime) -> GuestContext:
    return GuestContext(
        hotel_name="فندق طيبة",
        now=now,
        season="normal",
        room="402",
        guest_name="أحمد",
        hk_window_raw="08:00-16:00",
        desk_status="staffed",
        group_mode="individual",
    )
