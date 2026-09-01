"""تصدير البيانات وتقرير الوردية."""

from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest

from daif.clock import now_riyadh
from daif.models import HandoffRecord, Message, Tenant, Ticket
from daif.reports import handoffs_csv, shift_report, shift_report_text, tickets_csv


@pytest.fixture
def busy_hotel(db):
    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    now = now_riyadh()
    db.add_all([
        Message(tenant_id=tenant.id, direction="in", text="سؤال ١", created_at=now - timedelta(hours=2)),
        Message(tenant_id=tenant.id, direction="out", text="جواب", in_scope=True,
                language="ar", created_at=now - timedelta(hours=2)),
        Message(tenant_id=tenant.id, direction="in", text="سؤال ٢", created_at=now - timedelta(hours=1)),
        Message(tenant_id=tenant.id, direction="out", text="تحويل", in_scope=False,
                language="ur", created_at=now - timedelta(hours=1)),
        # خارج نافذة الوردية
        Message(tenant_id=tenant.id, direction="in", text="قديم", created_at=now - timedelta(days=3)),
        Ticket(tenant_id=tenant.id, type="cleaning", room="402", detail="تنظيف", status="open"),
        Ticket(tenant_id=tenant.id, type="maintenance", room="511", detail="مكيف",
               status="open", urgency="urgent"),
        Ticket(tenant_id=tenant.id, type="towels", room="120", detail="مناشف", status="done"),
        HandoffRecord(tenant_id=tenant.id, reason="no_documented_answer", to="front_desk",
                      guest_text="كم تبعد جدة؟", status="open"),
    ])
    db.flush()
    return tenant


def parse(body: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body.lstrip("﻿"))))


# --- التصدير ---------------------------------------------------------------

def test_export_starts_with_bom_so_excel_reads_arabic(busy_hotel, db):
    """بلا BOM يفتح إكسل العربية طلاسم — والمدير يظنّ المنتج معطوبًا."""
    assert tickets_csv(db, busy_hotel.id).startswith("﻿")


def test_tickets_export_has_header_and_rows(busy_hotel, db):
    rows = parse(tickets_csv(db, busy_hotel.id))
    assert rows[0][:3] == ["التاريخ", "الغرفة", "النوع"]
    assert len(rows) == 4  # ترويسة + ٣ تذاكر
    assert {r[1] for r in rows[1:]} == {"402", "511", "120"}


def test_handoffs_export_carries_the_guest_question(busy_hotel, db):
    rows = parse(handoffs_csv(db, busy_hotel.id))
    assert "كم تبعد جدة؟" in rows[1]


def test_export_is_scoped_to_one_hotel(busy_hotel, db):
    other = Tenant(slug="anwar", name="الأنوار")
    db.add(other)
    db.flush()
    db.add(Ticket(tenant_id=other.id, type="cleaning", room="999", detail="غير ظاهرة"))
    db.flush()
    assert "999" not in tickets_csv(db, busy_hotel.id)
    assert len(parse(tickets_csv(db, other.id))) == 2


# --- تقرير الوردية ---------------------------------------------------------

def test_shift_counts_only_the_window(busy_hotel, db):
    report = shift_report(db, busy_hotel.id, hours=12)
    assert report.inbound == 2          # الرسالة القديمة خارج النافذة
    assert report.answered == 1
    assert report.handed_off == 1


def test_automation_rate(busy_hotel, db):
    assert shift_report(db, busy_hotel.id, hours=12).automation_rate == 50


def test_open_work_is_carried_over_regardless_of_age(busy_hotel, db):
    """المعلّق يبقى معلّقًا: مناوب الليل يحتاج ما لم يُغلق، لا ما حدث في نوبته فقط."""
    report = shift_report(db, busy_hotel.id, hours=1)
    assert report.tickets_open_now == 2
    assert report.urgent_open == 1
    assert report.handoffs_open_now == 1
    assert report.needs_attention is True


def test_quiet_shift_needs_no_attention(db):
    tenant = Tenant(slug="quiet", name="هادئ")
    db.add(tenant)
    db.flush()
    report = shift_report(db, tenant.id)
    assert report.needs_attention is False
    assert report.automation_rate == 0


def test_text_report_is_short_and_complete(busy_hotel, db):
    text = shift_report_text(shift_report(db, busy_hotel.id, hours=12), "فندق طيبة")
    assert "فندق طيبة" in text
    assert "عاجلة" in text
    assert len(text.splitlines()) <= 16   # يُقرأ من إشعار جوال


def test_languages_are_reported(busy_hotel, db):
    codes = dict(shift_report(db, busy_hotel.id, hours=12).languages)
    assert codes == {"ar": 1, "ur": 1}
