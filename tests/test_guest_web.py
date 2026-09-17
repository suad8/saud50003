"""صفحة النزيل من طرف إلى طرف — بالطلبات الحقيقية لا باستدعاء الدوال."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ.setdefault("DAIF_SECRET_KEY", "test-key-for-guest-web")

from daif import roomqr, stay as stays          # noqa: E402
from daif.clock import now_riyadh               # noqa: E402
from daif.models import Tenant                  # noqa: E402


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


def path(room="402", slug="taibah"):
    return roomqr.sticker_path(slug, room)


def test_forged_signature_is_refused(client, hotel):
    r = client.get("/g/taibah/402/zzzzzzzzzz")
    assert r.status_code == 200
    assert "مو صحيح" in r.text


def test_guessing_a_room_number_is_refused(client, hotel, db):
    """كتابة رقم غرفة في شريط العنوان لا تفتح بابًا: توقيع ٤٠٢ لا يصلح لـ٣١٨."""
    stays.open_stay(db, hotel.id, "318", checkout_on=today() + timedelta(days=2))
    r = client.get("/g/taibah/318/" + roomqr.sign("taibah", "402"))
    assert "مو صحيح" in r.text


def test_scan_asks_for_the_code(client, hotel, db):
    stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    r = client.get(path())
    assert r.status_code == 200
    assert "رمز الإقامة" in r.text
    assert "العربية" in r.text and "اردو" in r.text


def test_empty_room_says_so_without_asking_for_a_code(client, hotel, db):
    r = client.get(path())
    assert "رمز الإقامة" in r.text          # الصفحة تُعرض
    sent = client.post(path() + "/enter", data={"language": "ar", "proof": "ABC123"})
    assert "ما فيه إقامة مفتوحة" in sent.text


def test_wrong_code_does_not_open_the_chat(client, hotel, db):
    stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    r = client.post(path() + "/enter", data={"language": "ar", "proof": "WRONG1"})
    assert "ما ضبط" in r.text
    assert "daif_stay_402" not in r.cookies


def test_correct_code_opens_the_chat_with_shortcuts(client, hotel, db):
    st = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    r = client.post(path() + "/enter", data={"language": "ar", "proof": st.stay_code},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "كلمة سر الواي فاي" in r.text      # الاختصارات ظاهرة
    assert "اكتب رسالتك" in r.text


def test_returning_guest_skips_the_code(client, hotel, db):
    st = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    client.post(path() + "/enter", data={"language": "ar", "proof": st.stay_code},
                follow_redirects=True)
    again = client.get(path(), follow_redirects=True)
    assert "اكتب رسالتك" in again.text


def test_chat_without_a_session_is_closed(client, hotel, db):
    stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    r = client.get(path() + "/chat")
    assert "ما نقدر نفتح المحادثة" in r.text


def test_checkout_closes_the_chat_mid_conversation(client, hotel, db):
    """النزيل واقف في الصفحة، والموظف يسجّل مغادرته. الرسالة التالية تُرفض."""
    st = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    client.post(path() + "/enter", data={"language": "ar", "proof": st.stay_code},
                follow_redirects=True)
    stays.close_stay(db, st, by="desk")
    db.commit()

    sent = client.post(path() + "/send", data={"text": "أبغى مناشف"})
    assert sent.status_code == 403
    assert sent.json()["error"] == "no_active_stay"

    page = client.get(path() + "/chat")
    assert "ما نقدر نفتح المحادثة" in page.text


def test_cookie_of_one_room_does_not_open_another(client, hotel, db):
    a = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    stays.open_stay(db, hotel.id, "318", checkout_on=today() + timedelta(days=2))
    client.post(path("402") + "/enter", data={"language": "ar", "proof": a.stay_code},
                follow_redirects=True)
    other = client.get(path("318") + "/chat")
    assert "ما نقدر نفتح المحادثة" in other.text


def test_poll_needs_a_live_session(client, hotel, db):
    st = stays.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    client.post(path() + "/enter", data={"language": "ar", "proof": st.stay_code},
                follow_redirects=True)
    assert client.get(path() + "/poll?after=0").status_code == 200
    stays.close_stay(db, st, by="pms")
    db.commit()
    assert client.get(path() + "/poll?after=0").status_code == 403
