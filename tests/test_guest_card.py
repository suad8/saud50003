"""من يكلّمه الموظف: اسم النزيل وغرفته وجواله في رأس المحادثة وفي القائمة.

كانت الشاشة تعرض «web:4» — معرّفًا داخليًّا لا يعني لموظف الاستقبال شيئًا.
والجوال مخزَّن على الإقامة لا على النزيل، فلم يكن يظهر أصلًا.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from daif import phones, stay as stays
from daif.clock import now_riyadh
from daif.models import Guest, StaffUser, Tenant
from daif.security import hash_password


@pytest.fixture
def desk(db):
    from fastapi.testclient import TestClient
    from daif import db as db_module
    from daif.web import app as app_module

    hotel = Tenant(slug="taibah", name="فندق طيبة")
    db.add(hotel)
    db.flush()
    db.add(StaffUser(tenant_id=hotel.id, email="owner@taibah.sa", name="ريم",
                     role="owner", password_hash=hash_password("pw12345678")))
    db.flush()

    app_module.app.dependency_overrides[db_module.get_session] = lambda: db
    with TestClient(app_module.app) as guest, TestClient(app_module.app) as staff:
        staff.get("/login")
        staff.post("/login", data={"email": "owner@taibah.sa", "password": "pw12345678",
                                   "csrf_token": staff.cookies.get("daif_csrf") or ""},
                   follow_redirects=True)
        yield hotel, guest, staff
    app_module.app.dependency_overrides.clear()


def arrive(db, hotel, guest_client, *, room="705", name="سعود العتيبي",
           phone="0532212529"):
    stay = stays.open_stay(db, hotel.id, room, guest_name=name, phone=phone,
                           checkout_on=now_riyadh().date() + timedelta(days=1))
    db.flush()
    guest_client.post("/h/taibah/enter", data={"phone": phone, "language": "ar"},
                      follow_redirects=False)
    guest_client.post("/h/taibah/send", data={"text": "فيه صيدلية قريبة؟"})
    return stay


def test_the_header_names_the_guest_with_room_and_phone(db, desk):
    hotel, guest, staff = desk
    arrive(db, hotel, guest)

    page = staff.get("/conversations").text
    assert "سعود العتيبي" in page
    assert "<b>705</b>" in page, "رقم الغرفة لا يظهر في رأس المحادثة"
    assert "053 221 2529" in page, "الجوال لا يظهر"
    assert 'href="tel:+966532212529"' in page, "الجوال ليس رابط اتصال"


def test_the_internal_id_is_no_longer_what_staff_read(db, desk):
    """«web:4» لا يعني لموظف الاستقبال شيئًا."""
    hotel, guest, staff = desk
    stay = arrive(db, hotel, guest)

    page = staff.get("/conversations").text
    assert f"web:{stay.id}" not in page


def test_the_list_shows_names_not_ids(db, desk):
    hotel, guest, staff = desk
    arrive(db, hotel, guest, room="705", name="سعود العتيبي", phone="0532212529")

    from fastapi.testclient import TestClient
    from daif.web import app as app_module
    with TestClient(app_module.app) as second:
        arrive(db, hotel, second, room="301", name="Siti Rahayu", phone="+62 812 000 111")

    page = staff.get("/conversations").text
    assert "سعود العتيبي" in page and "Siti Rahayu" in page
    assert "+62812000111" in page, "الرقم الأجنبي يُعرض دوليًّا"


def test_a_departed_guest_is_flagged_before_staff_type_a_reply(db, desk):
    """ردٌّ على من غادر لا يصله — صفحته أُقفلت. يُقال قبل أن يُكتب."""
    hotel, guest, staff = desk
    stay = arrive(db, hotel, guest)
    stays.close_stay(db, stay)
    db.flush()

    assert "لن يصله الردّ" in staff.get("/conversations").text


def test_a_staying_guest_shows_until_when(db, desk):
    hotel, guest, staff = desk
    arrive(db, hotel, guest)
    assert "مقيم حتى" in staff.get("/conversations").text


def test_no_guessing_by_room(db, desk):
    """محادثة بلا إقامة معروفة لا تُنسب لنزيل الغرفة الحالي.

    ذاك يُظهر اسم شخصٍ وجواله على محادثة شخصٍ آخر.
    """
    hotel, guest, staff = desk
    arrive(db, hotel, guest, room="705", name="سعود العتيبي", phone="0532212529")
    stranger = Guest(tenant_id=hotel.id, wa_id="legacy:1", room="705", language="ar")
    db.add(stranger)
    db.flush()

    from daif.repository import guest_cards
    card = guest_cards(db, hotel.id, [stranger])[stranger.id]
    assert card["name"] == "نزيل غرفة 705"
    assert card["phone"] == "", "نُسب جوال نزيل الغرفة لمحادثة غيره"


def test_every_demo_conversation_has_a_name_and_a_phone(db):
    """محادثة بلا اسم في العرض تبدو ناقصة في الشاشة التي يُفترض أن تبيّنها."""
    from daif import demo as demo_mod
    from daif.repository import guest_cards, list_guests

    demo_mod.seed(db)
    db.flush()
    tenant = db.scalar(select(Tenant))
    guests = [g for g in list_guests(db, tenant.id) if g.wa_id.startswith("web:")]
    cards = guest_cards(db, tenant.id, guests)

    assert guests
    for guest in guests:
        assert cards[guest.id]["named"], f"محادثة غرفة {guest.room} بلا اسم"
        assert cards[guest.id]["phone"], f"محادثة غرفة {guest.room} بلا جوال"


@pytest.mark.parametrize("stored,shown", [
    ("966532212529", "053 221 2529"),
    ("966500000001", "050 000 0001"),
    ("923001234567", "+923001234567"),
    ("", ""),
])
def test_phone_display(stored, shown):
    assert phones.display(stored) == shown
