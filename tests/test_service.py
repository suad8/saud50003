"""اختبارات التدفق الكامل: رسالة واردة ← نموذج ← تذكرة أو تحويل ← سجل."""

from __future__ import annotations

import pytest

from daif.assistant import Assistant
from daif.models import Fact, Guest, Tenant
from daif.repository import get_or_create_guest, list_handoffs, list_messages, list_tickets
from daif.schema import GuestReply, ServiceRequest
from daif.service import handle_inbound


@pytest.fixture
def hotel(db):
    tenant = Tenant(slug="taibah", name="فندق طيبة", wa_phone_number_id="PN_A",
                    hk_window="08:00-16:00")
    db.add(tenant)
    db.flush()
    db.add_all([
        Fact(tenant_id=tenant.id, key="K01", text="الواي فاي: Taibah-Guest / welcome2026"),
        Fact(tenant_id=tenant.id, key="K09", text="التنظيف من ٨ إلى ٤.", hours="08:00-16:00"),
    ])
    db.flush()
    return tenant


def guest_with_room(db, tenant, wa_id="966500000001", room="402"):
    guest = get_or_create_guest(db, tenant.id, wa_id)
    guest.room = room
    db.flush()
    return guest


def test_answer_is_persisted_with_metadata(db, hotel, fake_reply):
    assistant = Assistant(client=fake_reply(
        GuestReply(intent="inquiry", in_scope=True, language="ar",
                   answer="الشبكة Taibah-Guest وكلمة السر welcome2026.",
                   sources=["K01"], confidence=0.96)
    ))
    guest_with_room(db, hotel)
    out = handle_inbound(db, hotel, wa_id="966500000001",
                         text="وش كلمة سر الواي فاي؟", assistant=assistant)
    assert out.ticket is None and out.handoff is None
    assert out.outbound.sources == "K01"
    assert out.outbound.language == "ar"
    assert out.outbound.cache_read_tokens == 1000
    assert len(list_messages(db, hotel.id)) == 2  # وارد + صادر


def test_request_opens_a_ticket(db, hotel, fake_reply):
    assistant = Assistant(client=fake_reply(
        GuestReply(intent="request", in_scope=True, language="ur",
                   answer="آپ کی درخواست بھیج دی گئی۔", sources=[],
                   request=ServiceRequest(type="towels", room="402", detail="طلب مناشف"),
                   confidence=0.92)
    ))
    guest_with_room(db, hotel)
    out = handle_inbound(db, hotel, wa_id="966500000001",
                         text="مجھے تولیے چاہئیں", assistant=assistant)
    tickets = list_tickets(db, hotel.id)
    assert len(tickets) == 1
    assert tickets[0].room == "402" and tickets[0].type == "towels"
    assert out.ticket is not None


def test_room_from_message_text_is_never_used(db, hotel, fake_reply):
    """النزيل مسجّل في ٤٠٢ لكنه كتب ٥١١ — لا تُفتح تذكرة للغرفة المكتوبة."""
    assistant = Assistant(client=fake_reply(
        GuestReply(intent="request", in_scope=True, language="ar",
                   answer="أُبلغ التدبير.", sources=[],
                   request=ServiceRequest(type="cleaning", room="511", detail="تنظيف"),
                   confidence=0.9)
    ))
    guest_with_room(db, hotel, room="402")
    out = handle_inbound(db, hotel, wa_id="966500000001",
                         text="نظفوا غرفة ٥١١", assistant=assistant)
    assert list_tickets(db, hotel.id) == []
    assert out.handoff is not None
    assert out.handoff.reason == "unverified_room"


def test_restricted_topic_never_reaches_the_guest(db, hotel, fake_reply):
    """حتى لو أجاب النموذج عن وقت الصلاة، لا يخرج الجواب."""
    assistant = Assistant(client=fake_reply(
        GuestReply(intent="inquiry", in_scope=True, language="ar",
                   answer="الفجر الساعة ٤:٣٠.", sources=["K01"], confidence=0.99)
    ))
    guest_with_room(db, hotel)
    out = handle_inbound(db, hotel, wa_id="966500000001",
                         text="متى صلاة الفجر؟", assistant=assistant)
    assert "٤:٣٠" not in out.reply_text
    assert out.handoff.reason == "restricted_topic"
    assert out.outbound.restricted_category == "prayer_times"


def test_voice_note_hands_off_without_calling_the_model(db, hotel, fake_reply):
    client = fake_reply(GuestReply(intent="inquiry", in_scope=True, language="ar",
                                   answer="…", sources=["K01"], confidence=0.9))
    assistant = Assistant(client=client)
    guest_with_room(db, hotel)
    out = handle_inbound(db, hotel, wa_id="966500000001", text="[رسالة صوتية]",
                         assistant=assistant, low_confidence_input=True)
    assert client.messages.calls == []  # لم يُستدعَ النموذج أصلًا
    assert out.handoff.reason == "low_confidence"


def test_model_failure_degrades_to_handoff(db, hotel):
    class Exploding:
        class messages:  # noqa: N801
            @staticmethod
            def parse(**_):
                raise RuntimeError("انقطاع في الشبكة")

    guest_with_room(db, hotel)
    out = handle_inbound(db, hotel, wa_id="966500000001", text="سؤال",
                         assistant=Assistant(client=Exploding()))
    assert out.result.degraded is True
    assert out.handoff is not None
    assert out.reply_text.strip()  # النزيل يتلقى ردًا دائمًا


def test_history_excludes_the_current_message(db, hotel, fake_reply):
    client = fake_reply(
        GuestReply(intent="inquiry", in_scope=True, language="ar", answer="نعم.",
                   sources=["K01"], confidence=0.9)
    )
    assistant = Assistant(client=client)
    guest_with_room(db, hotel)
    handle_inbound(db, hotel, wa_id="966500000001", text="سؤال أول", assistant=assistant)
    handle_inbound(db, hotel, wa_id="966500000001", text="سؤال ثانٍ", assistant=assistant)

    first, second = client.messages.calls
    assert [m["role"] for m in first["messages"]] == ["user", "system"]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user", "system"]
    # لا تكرار لرسالة النزيل الحالية
    assert [m["content"] for m in second["messages"]].count("سؤال ثانٍ") == 1


def test_operating_context_travels_on_the_operator_channel(db, hotel, fake_reply):
    """قيم السياق تصل عبر رسالة مشغّل، لا داخل نص النزيل."""
    client = fake_reply(
        GuestReply(intent="inquiry", in_scope=True, language="ar", answer="نعم.",
                   sources=["K01"], confidence=0.9)
    )
    guest_with_room(db, hotel, room="402")
    handle_inbound(db, hotel, wa_id="966500000001", text="سؤال",
                   assistant=Assistant(client=client))
    call = client.messages.calls[0]
    operator = call["messages"][-1]
    assert operator["role"] == "system"
    assert "Guest room:          402" in operator["content"]
    assert "cache_control" in call["system"][0]


def test_handoff_records_the_guest_question_for_gap_analysis(db, hotel, fake_reply):
    assistant = Assistant(client=fake_reply(
        GuestReply(intent="out_of_scope", in_scope=False, language="ar",
                   answer="سيوافيك الاستقبال بذلك.", sources=[],
                   handoff={"reason": "no_documented_answer", "to": "front_desk",
                            "note": "خارج النطاق"},
                   confidence=0.97)
    ))
    guest_with_room(db, hotel)
    handle_inbound(db, hotel, wa_id="966500000001",
                   text="كم تبعد جدة عن المدينة؟", assistant=assistant)
    records = list_handoffs(db, hotel.id)
    assert records[0].guest_text == "كم تبعد جدة عن المدينة؟"
