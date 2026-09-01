"""ثغرات وجدتها المراجعة الأمنية الأخيرة — كل واحدة باختبار يمنع رجوعها."""

from __future__ import annotations

import time

import pytest

from daif.assistant import Assistant
from daif.models import Fact, Tenant
from daif.repository import get_or_create_guest, list_messages, list_tickets
from daif.reports import _safe_cell, handoffs_csv
from daif.schema import GuestReply, ServiceRequest
from daif.security import hash_password, verify_password_constant_time
from daif.service import handle_inbound


# --- ١) إعادة إرسال الـwebhook -------------------------------------------

@pytest.fixture
def hotel(db):
    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    db.add(Fact(tenant_id=tenant.id, key="K01", text="مناشف من التدبير."))
    guest = get_or_create_guest(db, tenant.id, "966500000001")
    guest.room = "402"
    db.flush()
    return tenant


def towels_reply() -> GuestReply:
    return GuestReply(
        intent="request", in_scope=True, language="ar", answer="أُبلغ التدبير.",
        sources=[],
        request=ServiceRequest(type="towels", room="402", detail="مناشف"),
        confidence=0.95,
    )


def test_redelivered_webhook_is_ignored(db, hotel, fake_reply):
    """Meta تعيد الإرسال عند أي تأخّر — بلا فحص يستلم النزيل ردّين."""
    client = fake_reply(towels_reply())
    assistant = Assistant(client=client)

    first = handle_inbound(db, hotel, wa_id="966500000001", text="أبغى مناشف",
                           assistant=assistant, wa_message_id="wamid.SAME")
    second = handle_inbound(db, hotel, wa_id="966500000001", text="أبغى مناشف",
                            assistant=assistant, wa_message_id="wamid.SAME")

    assert first is not None
    assert second is None
    assert len(client.messages.calls) == 1        # لم يُستدعَ النموذج مرتين
    assert len(list_tickets(db, hotel.id)) == 1   # ولا فُتحت تذكرتان
    assert len(list_messages(db, hotel.id)) == 2  # وارد + صادر فقط


def test_different_message_ids_both_processed(db, hotel, fake_reply):
    assistant = Assistant(client=fake_reply(towels_reply(), towels_reply()))
    assert handle_inbound(db, hotel, wa_id="966500000001", text="أ",
                          assistant=assistant, wa_message_id="wamid.1") is not None
    assert handle_inbound(db, hotel, wa_id="966500000001", text="ب",
                          assistant=assistant, wa_message_id="wamid.2") is not None
    assert len(list_tickets(db, hotel.id)) == 2


def test_missing_message_id_still_processed(db, hotel, fake_reply):
    """المحاكي والاختبارات لا تمرّر معرّف رسالة — يجب ألا يُحسب تكرارًا."""
    assistant = Assistant(client=fake_reply(towels_reply(), towels_reply()))
    assert handle_inbound(db, hotel, wa_id="966500000001", text="أ", assistant=assistant)
    assert handle_inbound(db, hotel, wa_id="966500000001", text="ب", assistant=assistant)


def test_same_id_in_another_hotel_is_not_a_duplicate(db, hotel, fake_reply):
    """معرّفات الرسائل تخصّ كل حساب واتساب — لا تُقارن عبر الفنادق."""
    other = Tenant(slug="anwar", name="الأنوار")
    db.add(other)
    db.flush()
    guest = get_or_create_guest(db, other.id, "966500000009")
    guest.room = "101"
    db.flush()

    assistant = Assistant(client=fake_reply(towels_reply(), towels_reply()))
    handle_inbound(db, hotel, wa_id="966500000001", text="أ",
                   assistant=assistant, wa_message_id="wamid.X")
    second = handle_inbound(db, other, wa_id="966500000009", text="أ",
                            assistant=assistant, wa_message_id="wamid.X")
    assert second is not None


# --- ٢) حقن الصيغ في CSV ---------------------------------------------------

@pytest.mark.parametrize("payload", ['=1+1', '+cmd', '-2+3', '@SUM(A1)', '\tمخفي', '\rعودة'])
def test_formula_starters_are_neutralised(payload):
    """نص التحويلات يأتي من النزيل — لو بدأ بـ= نفّذه إكسل صيغةً."""
    assert _safe_cell(payload).startswith("'")


@pytest.mark.parametrize("payload", ['نص عادي', 'K01', '402', 'أبغى مناشف', ''])
def test_ordinary_text_is_untouched(payload):
    assert _safe_cell(payload) == payload


def test_guest_text_in_export_is_neutralised(db, hotel):
    from daif.models import HandoffRecord

    db.add(HandoffRecord(
        tenant_id=hotel.id, reason="no_documented_answer", to="front_desk",
        guest_text='=HYPERLINK("http://evil","اضغط")',
    ))
    db.flush()
    body = handoffs_csv(db, hotel.id)
    assert '"\'=HYPERLINK' in body
    # الصيغة الخام غير موجودة كما هي في بداية خلية
    assert ',=HYPERLINK' not in body


def test_none_becomes_empty_not_the_word_none():
    assert _safe_cell(None) == ""


# --- ٣) تسريب وجود البريد عبر الزمن ----------------------------------------

def test_unknown_email_costs_the_same_time_as_a_wrong_password():
    stored = hash_password("kalimat-sirr-1")

    def elapsed(value):
        start = time.perf_counter()
        verify_password_constant_time("wrong-guess", value)
        return time.perf_counter() - start

    known = min(elapsed(stored) for _ in range(3))
    unknown = min(elapsed(None) for _ in range(3))
    # الفرق يجب أن يكون ضئيلًا مقارنةً بزمن التجزئة نفسه
    assert abs(known - unknown) < known * 0.5


def test_constant_time_check_still_decides_correctly():
    stored = hash_password("kalimat-sirr-1")
    assert verify_password_constant_time("kalimat-sirr-1", stored) is True
    assert verify_password_constant_time("wrong", stored) is False
    assert verify_password_constant_time("kalimat-sirr-1", None) is False
    assert verify_password_constant_time("anything", "") is False
