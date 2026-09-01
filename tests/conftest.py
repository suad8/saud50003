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


@pytest.fixture
def db(tmp_path):
    """قاعدة بيانات معزولة لكل اختبار."""
    from daif import db as db_module

    url = f"sqlite:///{tmp_path}/test.db"
    db_module.init_db(url)
    session = db_module.session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        db_module._engine = None
        db_module._SessionFactory = None


@pytest.fixture
def fake_reply():
    """يبني عميل نموذج وهميًا يعيد ردودًا محضّرة."""
    from types import SimpleNamespace

    def build(*replies):
        queue = list(replies)

        class Messages:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def parse(self, **kwargs):
                self.calls.append(kwargs)
                reply = queue.pop(0) if queue else replies[-1]
                return SimpleNamespace(
                    parsed_output=reply,
                    usage=SimpleNamespace(
                        input_tokens=1200,
                        output_tokens=60,
                        cache_read_input_tokens=1000,
                        cache_creation_input_tokens=0,
                    ),
                    _request_id="req_test",
                )

        class Client:
            def __init__(self) -> None:
                self.messages = Messages()

        return Client()

    return build
