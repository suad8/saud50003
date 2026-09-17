"""لوحة الإقامات وورقة الملصقات."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ.setdefault("DAIF_SECRET_KEY", "test-key-for-stays-dash")

from daif import roomqr, stay as stays          # noqa: E402
from daif.clock import now_riyadh               # noqa: E402
from daif.models import StaffUser, Tenant       # noqa: E402
from daif.security import hash_password         # noqa: E402
from daif.web.app import _room_list             # noqa: E402


def today():
    return now_riyadh().date()


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    from daif import db as db_module
    from daif.web import app as app_module

    app_module.app.dependency_overrides[db_module.get_session] = lambda: db
    with TestClient(app_module.app) as c:
        yield c
    app_module.app.dependency_overrides.clear()


@pytest.fixture
def hotel(db):
    t = Tenant(slug="taibah", name="فندق طيبة")
    db.add(t)
    db.flush()
    for email, role in [("owner@taibah.sa", "owner"), ("desk@taibah.sa", "staff")]:
        db.add(StaffUser(tenant_id=t.id, email=email, name=email, role=role,
                         password_hash=hash_password("pw12345678")))
    db.flush()
    return t


def login(client, email="owner@taibah.sa"):
    page = client.get("/login")
    token = client.cookies.get("daif_csrf") or ""
    r = client.post("/login", data={"email": email, "password": "pw12345678",
                                    "csrf_token": token}, follow_redirects=True)
    return r


# --- مدى الغرف --------------------------------------------------------------

def test_room_range_expands():
    assert _room_list("401-405") == ["401", "402", "403", "404", "405"]


def test_room_list_mixes_ranges_and_singles():
    assert _room_list("101-103, 501، 502") == ["101", "102", "103", "501", "502"]


def test_room_range_keeps_leading_zeros():
    assert _room_list("008-010") == ["008", "009", "010"]


def test_room_list_drops_duplicates_keeping_order():
    assert _room_list("402, 401-402, 402") == ["402", "401"]


def test_absurd_range_is_capped_not_expanded():
    """مدى مفتوح كان يولّد آلاف الملصقات ويعلّق المتصفح."""
    assert len(_room_list("1-99999")) == 200


def test_backwards_range_is_left_alone():
    assert _room_list("410-401") == ["410-401"]


# --- الصفحة -----------------------------------------------------------------

def test_stays_page_needs_login(client, hotel):
    r = client.get("/stays", follow_redirects=False)
    assert r.status_code in (303, 307)


def test_desk_can_check_in_and_sees_the_code(client, hotel, db):
    login(client, "desk@taibah.sa")
    token = client.cookies.get("daif_csrf") or ""
    r = client.post("/stays/checkin",
                    data={"room": "402", "guest_name": "أحمد", "phone": "966500000001",
                          "nights": "3", "csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200
    stay = stays.active_stay(db, hotel.id, "402")
    assert stay is not None and stay.stay_code
    assert stay.stay_code in r.text          # الموظف يقرأه ليطبعه للنزيل


def test_checkin_closes_the_previous_stay_in_that_room(client, hotel, db):
    old = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=5))
    db.commit()
    login(client, "desk@taibah.sa")
    token = client.cookies.get("daif_csrf") or ""
    client.post("/stays/checkin", data={"room": "402", "nights": "2", "csrf_token": token},
                follow_redirects=True)
    db.refresh(old)
    assert old.status == "closed"


def test_checkout_from_the_desk_kills_the_devices(client, hotel, db):
    st = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    access = stays.enter(db, hotel.id, "402", proof=st.stay_code)
    assert access.granted
    db.commit()

    login(client, "desk@taibah.sa")
    token = client.cookies.get("daif_csrf") or ""
    client.post(f"/stays/{st.id}/checkout", data={"csrf_token": token},
                follow_redirects=True)

    db.refresh(st)
    assert st.status == "closed" and st.closed_by == "desk"
    assert not stays.check(db, hotel.id, "402", access.token).granted


def test_revoking_one_device_leaves_the_stay_open(client, hotel, db):
    """نزيل فقد جواله: يُسحب جهازه وحده، والإقامة وبقية العائلة تكمل."""
    st = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    lost = stays.enter(db, hotel.id, "402", proof=st.stay_code)
    kept = stays.enter(db, hotel.id, "402", proof=st.stay_code)
    db.commit()
    device_id = st.devices[0].id

    login(client, "desk@taibah.sa")
    token = client.cookies.get("daif_csrf") or ""
    client.post(f"/stays/{st.id}/devices/{device_id}/revoke",
                data={"csrf_token": token}, follow_redirects=True)

    db.refresh(st)
    assert st.status == "open"
    assert not stays.check(db, hotel.id, "402", lost.token).granted
    assert stays.check(db, hotel.id, "402", kept.token).granted


def test_a_hotel_cannot_touch_another_hotels_stay(client, hotel, db):
    """العزل بين الفنادق يسري على كل إجراء لا على العرض وحده."""
    rival = Tenant(slug="anwar", name="فندق الأنوار")
    db.add(rival)
    db.flush()
    theirs = stays.open_stay(db, rival.id, "402", checkout_on=today() + timedelta(days=3))
    db.commit()

    login(client, "desk@taibah.sa")
    token = client.cookies.get("daif_csrf") or ""
    r = client.post(f"/stays/{theirs.id}/checkout", data={"csrf_token": token})
    assert r.status_code == 404
    db.refresh(theirs)
    assert theirs.status == "open"


# --- الملصقات ---------------------------------------------------------------

def test_stickers_render_real_scannable_codes(client, hotel):
    login(client)
    r = client.get("/stays/stickers?rooms=401-403")
    assert r.status_code == 200
    assert r.text.count("<svg") == 3
    assert "غرفة 401" in r.text and "غرفة 403" in r.text


def test_the_printed_sticker_opens_the_guest_door(client, hotel, db):
    """أهم اختبار في الصفحة: الرابط الذي يحمله الملصق يفتح صفحة النزيل فعلًا.

    الرابط لا يظهر كنصّ في الملصق — هو داخل وحدات الرمز. فنأخذه من نفس
    الدالة التي تبني الملصق ونطرق به الباب.
    """
    login(client)
    assert client.get("/stays/stickers?rooms=402").status_code == 200

    url = roomqr.sheet("http://testserver", "taibah", ["402"])[0].url
    stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    db.commit()

    landing = client.get(url.replace("http://testserver", ""))
    assert landing.status_code == 200
    assert "رمز الإقامة" in landing.text      # صفحة اللغة والإثبات، لا رفض


def test_a_sticker_for_another_hotel_does_not_open_this_one(client, hotel, db):
    login(client)
    stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    db.commit()
    theirs = roomqr.sticker_path("anwar", "402")          # نفس الغرفة، فندق آخر
    assert "مو صحيح" in client.get(theirs.replace("/anwar/", "/taibah/")).text


def test_desk_cannot_print_stickers(client, hotel):
    """الطباعة إعداد لا تشغيل — موظف الاستقبال لا يصدر أكواد غرف."""
    login(client, "desk@taibah.sa")
    assert client.get("/stays/stickers?rooms=402").status_code == 403
