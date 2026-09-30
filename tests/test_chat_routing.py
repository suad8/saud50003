"""مسار الرسالة في الشات: الاختصار يُجاب، والمكتوب يذهب للموظف.

القاعدة التي اختارها الفندق: لا يُخمَّن للنزيل جواب على ما يكتبه بيده. السؤال
المكتوب قد يكون شكوى أو طلبًا أو ظرفًا خاصًّا، وإنسانٌ يقرؤه أصدق من أي
مطابقة — ويبقى الفندق صاحب كل كلمة تصل نزيله.

والاختصار غير ذلك: سؤال معروف سلفًا، وجوابه حقيقة موثّقة تُقتبس كما هي.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from daif import stay as stays
from daif.clock import now_riyadh
from daif.models import Fact, HandoffRecord, Message, Tenant, Ticket

DOOR = "/h/taibah"
PHONE = "0501234567"


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
    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    for key, topic, text in [
        ("K01", "wifi", "الواي فاي: الشبكة Taibah-Guest، كلمة السر welcome2026."),
        ("K06", "haram_gate", "أقرب باب للحرم: باب الملك فهد رقم ٢١."),
    ]:
        db.add(Fact(tenant_id=tenant.id, key=key, topic=topic, text=text, active=True,
                    seasons="normal,ramadan,hajj"))
    db.flush()
    return tenant


@pytest.fixture
def guest(client, db, hotel):
    stays.open_stay(db, hotel.id, "402", guest_name="أحمد", phone=PHONE,
                    checkout_on=now_riyadh().date() + timedelta(days=2))
    client.post(f"{DOOR}/enter", data={"phone": PHONE, "language": "ar"},
                follow_redirects=False)
    return client


def say(client, text, shortcut=""):
    return client.post(f"{DOOR}/send", data={"text": text, "shortcut": shortcut}).json()


# --- الاختصار يُجاب فورًا ---


def test_a_shortcut_is_answered_from_the_knowledge_base(guest):
    out = say(guest, "وش كلمة سر الواي فاي؟", shortcut="wifi")["messages"][1]

    assert "Taibah-Guest" in out["text"]
    assert not out.get("ticket")


def test_an_answered_shortcut_does_not_disturb_the_front_desk(guest, db, hotel):
    say(guest, "وش أقرب باب للحرم؟", shortcut="gate")

    assert db.scalars(select(Ticket)).all() == []
    assert db.scalars(select(HandoffRecord)).all() == []


def test_a_shortcut_with_no_documented_fact_goes_to_the_desk(guest, db):
    """حقيقة محجوبة أو ناقصة لا يُخترع لها بديل."""
    out = say(guest, "متى الإفطار ووين؟", shortcut="breakfast")["messages"][1]

    assert "وصل سؤالك للاستقبال" in out["text"]
    assert db.scalars(select(Ticket)).all()


def test_a_service_shortcut_opens_a_ticket_not_an_answer(guest, db):
    """«أبغى مناشف» طلبٌ لا سؤال — وهو ما يريده النزيل من الزر."""
    out = say(guest, "أبغى مناشف إضافية للغرفة", shortcut="towels")["messages"][1]

    assert out.get("ticket")
    assert db.scalars(select(Ticket)).all()


# --- المكتوب بيده يذهب للموظف دائمًا ---


@pytest.mark.parametrize("written", [
    "وش كلمة سر الواي فاي؟",          # يغطّيها اختصار، ومع ذلك لا تُجاب
    "أقرب باب للحرم",
    "فيه صيدلية قريبة؟",
    "المكيف ما يبرّد",
])
def test_anything_typed_by_hand_reaches_a_human(guest, db, written):
    out = say(guest, written)["messages"][1]

    assert out["text"] == "وصل سؤالك للاستقبال، وبيردّون عليك هنا."
    assert out["ticket"] is True


def test_the_typed_question_reaches_the_desk_word_for_word(guest, db, hotel):
    """الموظف يقرأ ما كتبه النزيل، لا ملخّصًا ولا إعادة صياغة."""
    written = "زوجتي حامل وتحتاج غرفة قريبة من المصعد لو ممكن"
    say(guest, written)

    handoff = db.scalar(select(HandoffRecord))
    ticket = db.scalar(select(Ticket))
    assert handoff.guest_text == written
    assert ticket.detail == written
    assert ticket.room == "402", "التذكرة بلا رقم غرفة لا تُخدَم"


def test_both_sides_of_the_exchange_are_recorded(guest, db):
    say(guest, "فيه مواقف للسيارات؟")

    rows = db.scalars(select(Message).order_by(Message.id)).all()
    assert [m.direction for m in rows] == ["in", "out"]
    assert rows[0].text == "فيه مواقف للسيارات؟"


def test_no_model_is_ever_called_for_typed_text(guest, db, monkeypatch):
    """القاعدة كلّها: النصّ الحرّ لا يمرّ على نموذج."""
    from daif.assistant import Assistant

    def explode(*_args, **_kwargs):
        raise AssertionError("استُدعي النموذج على نصّ كتبه النزيل")

    monkeypatch.setattr(Assistant, "reply", explode)
    assert say(guest, "أي سؤال كان")["messages"][1]["ticket"] is True


# --- اللغتان ---


def test_the_door_offers_arabic_and_english_only(client, hotel, db):
    stays.open_stay(db, hotel.id, "402", phone=PHONE,
                    checkout_on=now_riyadh().date() + timedelta(days=2))
    body = client.get(DOOR).text

    assert 'data-lang="ar"' in body and 'data-lang="en"' in body
    for gone in ("ur", "id", "tr", "bn", "fa", "fr", "ms", "ha"):
        assert f'data-lang="{gone}"' not in body, f"لغة زائدة: {gone}"


# --- إشعار الاستقبال ---


def test_the_pulse_counts_what_is_waiting(guest, client, db, hotel):
    from daif.models import StaffUser
    from daif.security import hash_password

    db.add(StaffUser(tenant_id=hotel.id, email="owner@taibah.sa", name="ريم",
                     role="owner", password_hash=hash_password("pw12345678")))
    db.flush()
    say(guest, "فيه صيدلية قريبة؟")

    client.get("/login")
    client.post("/login", data={"email": "owner@taibah.sa", "password": "pw12345678",
                                "csrf_token": client.cookies.get("daif_csrf") or ""},
                follow_redirects=True)
    body = client.get("/pulse").json()

    assert body["tickets"] == 1 and body["handoffs"] == 1


def test_the_pulse_is_not_public(client):
    """عدّادات فندقٍ لا تُقرأ بلا تسجيل دخول."""
    assert client.get("/pulse").status_code == 401
