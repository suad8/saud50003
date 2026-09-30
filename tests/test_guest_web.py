"""باب النزيل الموحّد من طرف إلى طرف — بالطلبات الحقيقية لا باستدعاء الدوال.

رابط واحد للفندق كله، والجوال هو المفتاح. الغرفة تُستنتج من الحجز لا من
الرابط — ولهذا وحده يعمل نزيل نُقل إلى غرفة أخرى.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ.setdefault("DAIF_SECRET_KEY", "test-key-for-guest-web")

from daif import stay as stays                  # noqa: E402
from daif.clock import now_riyadh               # noqa: E402
from daif.models import Tenant                  # noqa: E402

DOOR = "/h/taibah"
PHONE = "0501234567"


def today():
    return now_riyadh().date()


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    from daif import db as db_module
    from daif.web import app as app_module

    # الطلبات تشترك مع الاختبار في نفس الجلسة، فما نكتبه هنا يراه الخادم.
    # لا نستبدل `get_session` نفسها: المسارات التقطت الدالة الأصلية عند
    # الاستيراد، فاستبدالها يجعل مفتاح التجاوز لا يطابق ما تعتمد عليه.
    app_module.app.dependency_overrides[db_module.get_session] = lambda: db
    with TestClient(app_module.app) as c:
        yield c
    app_module.app.dependency_overrides.clear()


@pytest.fixture
def hotel(db):
    t = Tenant(slug="taibah", name="فندق طيبة")
    db.add(t)
    db.flush()
    return t


def check_in(db, hotel, room="402", phone=PHONE, nights=2):
    return stays.open_stay(db, hotel.id, room, guest_name="أحمد", phone=phone,
                           checkout_on=today() + timedelta(days=nights))


def knock(client, phone=PHONE, room="", language="ar"):
    return client.post(f"{DOOR}/enter",
                       data={"phone": phone, "room": room, "language": language},
                       follow_redirects=False)


# --- الباب -------------------------------------------------------------------


def test_an_unknown_hotel_link_is_refused(client, hotel):
    assert "مو صحيح" in client.get("/h/nope").text


def test_the_door_asks_for_a_phone_and_nothing_else(client, hotel, db):
    check_in(db, hotel)
    body = client.get(DOOR).text

    assert 'name="phone"' in body
    assert 'name="room"' not in body, "الغرفة تُستنتج من الحجز لا تُسأل"
    assert "402" not in body, "الباب يعرض رقم غرفة قبل أي إثبات"


def test_the_registered_phone_opens_the_chat(client, hotel, db):
    check_in(db, hotel)
    response = knock(client)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/chat")


@pytest.mark.parametrize("typed", [
    "0501234567", "+966501234567", "966501234567", "501234567",
    "050 123 4567", "٠٥٠١٢٣٤٥٦٧",
])
def test_every_way_of_writing_the_number_works(client, hotel, db, typed):
    """رقم صحيح يُرفض لأن صورته غير صورة المخزَّن عطلٌ لا يفهمه أحد."""
    check_in(db, hotel, phone="+966 50 123 4567")

    assert knock(client, phone=typed).status_code == 303


def test_an_unregistered_phone_does_not_open_the_chat(client, hotel, db):
    check_in(db, hotel)
    response = knock(client, phone="0509999999")

    assert response.status_code == 200
    assert "ما لقينا إقامة" in response.text


def test_a_wrong_number_and_no_stay_read_the_same(client, hotel, db):
    """التفريق بينهما يقول لمن يجرّب أرقامًا إن هذا الرقم نازلٌ هنا."""
    check_in(db, hotel)
    wrong = knock(client, phone="0509999999").text

    stays.close_stay(db, stays.active_stay(db, hotel.id, "402"))
    db.flush()
    client.cookies.clear()
    gone = knock(client, phone=PHONE).text

    assert "ما لقينا إقامة" in wrong and "ما لقينا إقامة" in gone


def test_gibberish_is_rejected_before_touching_the_database(client, hotel, db):
    check_in(db, hotel)
    assert "بالأرقام" in knock(client, phone="مرحبا").text


# --- رقم واحد على عدّة غرف ----------------------------------------------------


def test_one_number_on_two_rooms_asks_which_room(client, hotel, db):
    """عائلة تحجز جناحين برقم واحد — حالة عادية لا استثناء."""
    check_in(db, hotel, room="402")
    check_in(db, hotel, room="403")

    response = knock(client)
    assert response.status_code == 200
    assert 'name="room"' in response.text


def test_then_the_right_room_opens_it(client, hotel, db):
    check_in(db, hotel, room="402")
    check_in(db, hotel, room="403")

    assert knock(client, room="403").status_code == 303


def test_and_a_room_that_is_not_theirs_does_not(client, hotel, db):
    check_in(db, hotel, room="402")
    check_in(db, hotel, room="403")
    check_in(db, hotel, room="911", phone="0505555555")   # غرفة شخص آخر

    response = knock(client, room="911")
    assert response.status_code == 200
    assert "ما ضبط" in response.text


# --- المحادثة ----------------------------------------------------------------


def test_the_chat_never_shows_the_room_number(client, hotel, db):
    """هو يعرف غرفته. المستفيد الوحيد من عرضها من كتب رقم جوال غيره."""
    check_in(db, hotel, room="402")
    knock(client)

    body = client.get(f"{DOOR}/chat").text
    assert "المحادثة" in body
    assert "402" not in body


def test_the_chat_carries_the_shortcuts(client, hotel, db):
    check_in(db, hotel)
    knock(client)

    body = client.get(f"{DOOR}/chat").text
    assert "الواي فاي" in body


def test_a_returning_guest_is_not_asked_again(client, hotel, db):
    check_in(db, hotel)
    knock(client)

    assert client.get(DOOR, follow_redirects=False).status_code == 303


def test_the_chat_without_a_session_is_closed(client, hotel, db):
    check_in(db, hotel)
    assert "انتهت" in client.get(f"{DOOR}/chat").text


def test_poll_needs_a_live_session(client, hotel, db):
    check_in(db, hotel)
    assert client.get(f"{DOOR}/poll").status_code == 403


# --- الحماية -----------------------------------------------------------------


def test_checkout_closes_the_chat_mid_conversation(client, hotel, db):
    """المغادرة تقع في منتصف المحادثة، لا بين الجلسات."""
    stay = check_in(db, hotel)
    knock(client)
    assert client.get(f"{DOOR}/chat").status_code == 200

    stays.close_stay(db, stay)
    db.flush()

    assert "انتهت" in client.get(f"{DOOR}/chat").text
    assert client.post(f"{DOOR}/send", data={"text": "أبي مناشف"}).status_code == 403


def test_a_cookie_from_one_hotel_does_not_open_another(client, hotel, db):
    """رمز جهاز صحيح في فندق لا يفتح بابًا في فندق آخر."""
    other = Tenant(slug="anwar", name="فندق الأنوار")
    db.add(other)
    db.flush()
    check_in(db, hotel)
    check_in(db, other, room="402", phone="0507777777")

    knock(client)
    stolen = client.cookies.get("daif_stay_taibah")
    assert stolen

    client.cookies.clear()
    client.cookies.set("daif_stay_anwar", stolen)
    assert "انتهت" in client.get("/h/anwar/chat").text


def test_a_new_guest_in_the_same_room_does_not_inherit_the_chat(client, hotel, db):
    """تسجيل وصول جديد يبطل أجهزة من غادر في نفس اللحظة."""
    check_in(db, hotel, room="402")
    knock(client)
    assert client.get(f"{DOOR}/chat").status_code == 200

    check_in(db, hotel, room="402", phone="0508888888")    # نزيل جديد، نفس الغرفة
    db.flush()

    assert "انتهت" in client.get(f"{DOOR}/chat").text
