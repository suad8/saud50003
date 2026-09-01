"""إدارة موظفي الفندق وقائمة التهيئة."""

from __future__ import annotations

import pytest

from daif.models import Fact, Guest, Message, StaffUser, Tenant
from daif.repository import count_active_owners, list_staff, onboarding_state
from daif.security import hash_password


@pytest.fixture
def hotel(db):
    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    db.add_all([
        StaffUser(tenant_id=tenant.id, email="owner@taibah.sa",
                  password_hash=hash_password("kalimat-sirr-1"), role="owner"),
        StaffUser(tenant_id=tenant.id, email="mgr@taibah.sa",
                  password_hash=hash_password("kalimat-sirr-2"), role="manager"),
        StaffUser(tenant_id=tenant.id, email="desk@taibah.sa",
                  password_hash=hash_password("kalimat-sirr-3"), role="staff"),
    ])
    db.flush()
    return tenant


# --- الموظفون ---------------------------------------------------------------

def test_staff_are_scoped_to_their_hotel(db, hotel):
    other = Tenant(slug="anwar", name="الأنوار")
    db.add(other)
    db.flush()
    db.add(StaffUser(tenant_id=other.id, email="x@anwar.sa",
                     password_hash=hash_password("kalimat-sirr-9"), role="owner"))
    db.flush()
    assert {u.email for u in list_staff(db, hotel.id)} == {
        "owner@taibah.sa", "mgr@taibah.sa", "desk@taibah.sa"
    }
    assert [u.email for u in list_staff(db, other.id)] == ["x@anwar.sa"]


def test_owner_count_ignores_disabled_and_other_roles(db, hotel):
    assert count_active_owners(db, hotel.id) == 1
    owner = list_staff(db, hotel.id)[0]
    owner.active = False
    db.flush()
    assert count_active_owners(db, hotel.id) == 0


def test_owner_count_is_per_hotel(db, hotel):
    other = Tenant(slug="anwar", name="الأنوار")
    db.add(other)
    db.flush()
    db.add(StaffUser(tenant_id=other.id, email="o@anwar.sa",
                     password_hash=hash_password("kalimat-sirr-9"), role="owner"))
    db.flush()
    assert count_active_owners(db, hotel.id) == 1
    assert count_active_owners(db, other.id) == 1


# --- قائمة التهيئة ----------------------------------------------------------

def test_fresh_hotel_has_nothing_done(db, hotel):
    state = onboarding_state(db, hotel)
    assert state["done"] == 0
    assert state["complete"] is False
    assert [s["key"] for s in state["steps"]] == ["facts", "whatsapp", "rooms", "test"]


def test_facts_step_needs_ten_active(db, hotel):
    for i in range(9):
        db.add(Fact(tenant_id=hotel.id, key=f"K{i:02d}", text="حقيقة", active=True))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][0]["done"] is False
    db.add(Fact(tenant_id=hotel.id, key="K99", text="عاشرة", active=True))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][0]["done"] is True


def test_disabled_facts_do_not_count(db, hotel):
    """الحقائق النموذجية تُنشأ معطّلة — عدّها يخدع المدير أن التهيئة تمّت."""
    for i in range(20):
        db.add(Fact(tenant_id=hotel.id, key=f"K{i:02d}", text="حقيقة", active=False))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][0]["done"] is False


def test_whatsapp_step_needs_both_id_and_token(db, hotel):
    hotel.wa_phone_number_id = "PN123"
    db.flush()
    assert onboarding_state(db, hotel)["steps"][1]["done"] is False
    hotel.wa_access_token = "enc:v1:xxx"
    db.flush()
    assert onboarding_state(db, hotel)["steps"][1]["done"] is True


def test_rooms_step_needs_a_verified_room(db, hotel):
    db.add(Guest(tenant_id=hotel.id, wa_id="966500000001", room=""))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][2]["done"] is False
    db.add(Guest(tenant_id=hotel.id, wa_id="966500000002", room="402"))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][2]["done"] is True


def test_test_step_needs_a_reply_sent(db, hotel):
    db.add(Message(tenant_id=hotel.id, direction="in", text="سؤال"))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][3]["done"] is False
    db.add(Message(tenant_id=hotel.id, direction="out", text="جواب"))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][3]["done"] is True


def test_complete_when_all_four_done(db, hotel):
    for i in range(10):
        db.add(Fact(tenant_id=hotel.id, key=f"K{i:02d}", text="حقيقة", active=True))
    hotel.wa_phone_number_id = "PN123"
    hotel.wa_access_token = "enc:v1:xxx"
    db.add_all([
        Guest(tenant_id=hotel.id, wa_id="966500000001", room="402"),
        Message(tenant_id=hotel.id, direction="out", text="جواب"),
    ])
    db.flush()
    state = onboarding_state(db, hotel)
    assert state["complete"] is True
    assert state["done"] == state["total"] == 4


def test_onboarding_is_per_hotel(db, hotel):
    other = Tenant(slug="anwar", name="الأنوار")
    db.add(other)
    db.flush()
    for i in range(12):
        db.add(Fact(tenant_id=hotel.id, key=f"K{i:02d}", text="حقيقة", active=True))
    db.flush()
    assert onboarding_state(db, hotel)["steps"][0]["done"] is True
    assert onboarding_state(db, other)["steps"][0]["done"] is False
