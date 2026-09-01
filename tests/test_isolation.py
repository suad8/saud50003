"""عزل البيانات بين الفنادق — أهم ضمان في منتج متعدد المشتركين."""

from __future__ import annotations

import pytest

from daif.models import Fact, Guest, HandoffRecord, StaffUser, Tenant, Ticket
from daif.repository import (
    knowledge_gaps,
    list_facts,
    list_guests,
    list_handoffs,
    list_messages,
    list_tickets,
    load_knowledge_base,
    next_fact_key,
    stats,
    tenant_by_phone_number_id,
)
from daif.security import hash_password


@pytest.fixture
def two_hotels(db):
    """فندقان، لكل منهما حقائقه ونزلاؤه وتذاكره."""
    a = Tenant(slug="taibah", name="فندق طيبة", wa_phone_number_id="PN_A")
    b = Tenant(slug="anwar", name="فندق الأنوار", wa_phone_number_id="PN_B")
    db.add_all([a, b])
    db.flush()

    db.add_all([
        Fact(tenant_id=a.id, key="K01", text="واي فاي طيبة: Taibah-Guest"),
        Fact(tenant_id=a.id, key="K02", text="إفطار طيبة ٥:٣٠"),
        Fact(tenant_id=b.id, key="K01", text="واي فاي الأنوار: Anwar-Net"),
    ])
    ga = Guest(tenant_id=a.id, wa_id="966500000001", room="402")
    gb = Guest(tenant_id=b.id, wa_id="966500000002", room="118")
    db.add_all([ga, gb])
    db.flush()

    db.add_all([
        Ticket(tenant_id=a.id, guest_id=ga.id, type="cleaning", room="402", detail="تنظيف"),
        Ticket(tenant_id=b.id, guest_id=gb.id, type="towels", room="118", detail="مناشف"),
        HandoffRecord(tenant_id=a.id, reason="no_documented_answer", to="front_desk",
                      guest_text="كم تبعد جدة؟"),
        HandoffRecord(tenant_id=b.id, reason="complaint", to="duty_manager",
                      guest_text="شكوى الأنوار"),
    ])
    db.flush()
    return a, b


def test_facts_do_not_leak(two_hotels, db):
    a, b = two_hotels
    assert {f.text for f in list_facts(db, a.id)} == {"واي فاي طيبة: Taibah-Guest", "إفطار طيبة ٥:٣٠"}
    assert {f.text for f in list_facts(db, b.id)} == {"واي فاي الأنوار: Anwar-Net"}


def test_same_fact_key_in_two_hotels_is_allowed(two_hotels, db):
    """K01 يخص كل فندق على حدة — لا تصادم بينهما."""
    a, b = two_hotels
    kb_a = load_knowledge_base(db, a.id)
    kb_b = load_knowledge_base(db, b.id)
    assert kb_a.get("K01").text != kb_b.get("K01").text


def test_next_key_counts_only_own_hotel(two_hotels, db):
    a, b = two_hotels
    assert next_fact_key(db, a.id) == "K03"
    assert next_fact_key(db, b.id) == "K02"


def test_guests_tickets_handoffs_do_not_leak(two_hotels, db):
    a, b = two_hotels
    assert [g.room for g in list_guests(db, a.id)] == ["402"]
    assert [t.room for t in list_tickets(db, a.id)] == ["402"]
    assert [h.reason for h in list_handoffs(db, b.id)] == ["complaint"]
    assert list_messages(db, a.id) == []


def test_gaps_are_scoped(two_hotels, db):
    a, b = two_hotels
    assert knowledge_gaps(db, a.id) == [("كم تبعد جدة؟", 1)]
    assert knowledge_gaps(db, b.id) == []


def test_stats_are_scoped(two_hotels, db):
    a, b = two_hotels
    assert stats(db, a.id)["open_tickets"] == 1
    assert stats(db, b.id)["open_tickets"] == 1
    assert stats(db, a.id)["open_handoffs"] == 1


def test_phone_number_routes_to_right_hotel(two_hotels, db):
    a, b = two_hotels
    assert tenant_by_phone_number_id(db, "PN_A").slug == "taibah"
    assert tenant_by_phone_number_id(db, "PN_B").slug == "anwar"
    assert tenant_by_phone_number_id(db, "PN_UNKNOWN") is None
    assert tenant_by_phone_number_id(db, "") is None


def test_inactive_hotel_is_not_routed(two_hotels, db):
    a, _ = two_hotels
    a.active = False
    db.flush()
    assert tenant_by_phone_number_id(db, "PN_A") is None


def test_staff_email_is_globally_unique(two_hotels, db):
    """موظف واحد لا ينتمي لفندقين — البريد مفتاح عالمي."""
    from sqlalchemy.exc import IntegrityError

    a, b = two_hotels
    db.add(StaffUser(tenant_id=a.id, email="x@y.sa", password_hash=hash_password("12345678")))
    db.flush()
    db.add(StaffUser(tenant_id=b.id, email="x@y.sa", password_hash=hash_password("12345678")))
    with pytest.raises(IntegrityError):
        db.flush()
