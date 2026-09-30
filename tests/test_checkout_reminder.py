"""تذكير المغادرة: مرّة واحدة، وفي وقت يصحّ أن يُوقظ فيه أحد.

النزيل ينسى ساعة المغادرة فيتأخّر ويُفاجأ برسم يوم إضافي، والاستقبال يقضي
صباحه يتّصل غرفةً غرفة. رسالة تسبق الموعد تحلّ الاثنين.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from daif import reminders, stay as stays
from daif.clock import RIYADH
from daif.models import Message, Tenant


def T(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=RIYADH)


@pytest.fixture
def hotel(db):
    t = Tenant(slug="taibah", name="فندق طيبة", checkout_reminder_hours=3)
    db.add(t)
    db.flush()
    return t


def check_in(db, hotel, room="402", out_day=16, out_hour=12):
    """إقامة تنتهي في وقت محدّد بالضبط.

    نضبط `expires_at` صراحةً ولا نمرّ بـ`default_expiry`: هي تقيس من الساعة
    الحقيقية وتضمن نافذة دنيا، فيصير الاختبار رهينة يوم تشغيله.
    """
    stay = stays.open_stay(db, hotel.id, room, guest_name="أحمد", phone="0501234567")
    stay.expires_at = T(out_day, out_hour) + stays.CHECKOUT_GRACE
    db.flush()
    return stay


def sent_texts(db, hotel):
    return [m.text for m in db.scalars(
        select(Message).where(Message.tenant_id == hotel.id,
                              Message.intent == "checkout_reminder"))]


# --- التوقيت ------------------------------------------------------------------


def test_it_arrives_three_hours_before_checkout(db, hotel):
    check_in(db, hotel)
    assert not reminders.send_due(db, hotel, at=T(16, 8, 30))   # مبكّر
    assert reminders.send_due(db, hotel, at=T(16, 9, 5))        # حان


def test_it_is_never_sent_twice(db, hotel):
    """المجدول يمرّ كل بضع دقائق — بلا علامة يصير التذكير سيلًا."""
    check_in(db, hotel)
    assert len(reminders.send_due(db, hotel, at=T(16, 9, 5))) == 1

    for minute in (10, 20, 40):
        assert reminders.send_due(db, hotel, at=T(16, 9, minute)) == []
    assert len(sent_texts(db, hotel)) == 1


def test_a_long_outage_does_not_send_a_reminder_after_checkout(db, hotel):
    """تذكيرٌ بالمغادرة يصل بعدها إزعاج لا خدمة."""
    check_in(db, hotel)
    assert reminders.send_due(db, hotel, at=T(16, 20)) == []


def test_turning_it_off_stops_it(db, hotel):
    hotel.checkout_reminder_hours = 0
    check_in(db, hotel)
    assert reminders.send_due(db, hotel, at=T(16, 9, 5)) == []


# --- ساعات النوم ---------------------------------------------------------------


def test_a_dawn_checkout_is_announced_the_night_before(db, hotel):
    """نزيل في مكة ينام بعد الفجر. تنبيهٌ صوتي الثالثة فجرًا أسوأ من لا تذكير.

    والمغادرة السادسة فجرًا، فالتأجيل للصباح يقع بعدها — يُقدَّم لليلة أمسها.
    """
    stay = check_in(db, hotel, out_hour=6)

    when = reminders.due_at(stay, 3)
    assert not reminders._in_quiet_hours(when)
    assert when.day == 15 and when.hour == 22         # ليلة أمس، لا ٣ فجرًا
    # ولا يرنّ الثالثة فجرًا ولو كان المجدول نائمًا عن موعده.
    assert reminders.send_due(db, hotel, at=T(16, 3)) == []


def test_a_midday_checkout_can_simply_wait_for_the_morning(db, hotel):
    """حين يسع التأجيل، لا داعي للتقديم لليلة أمس."""
    stay = check_in(db, hotel, out_hour=9)
    assert reminders.due_at(stay, 3) == T(16, 7, 30)


@pytest.mark.parametrize("out_day,out_hour", [(16, 6), (17, 2), (16, 3)])
def test_a_reminder_is_never_pushed_past_the_checkout_it_announces(
    db, hotel, out_day, out_hour
):
    """مغادرة فجرية: التأجيل للصباح يقع بعدها، فنُقدّم بدل أن نُؤجّل."""
    stay = check_in(db, hotel, out_day=out_day, out_hour=out_hour)
    when = reminders.due_at(stay, 3)

    assert when < reminders.checkout_at(stay), "تذكير بمغادرة بعد وقوعها"
    assert not reminders._in_quiet_hours(when), "تذكير في ساعات النوم"


def test_daytime_reminders_are_not_shifted(db, hotel):
    stay = check_in(db, hotel)
    assert reminders.due_at(stay, 3) == T(16, 9)


# --- المحتوى والوجهة -----------------------------------------------------------


def test_the_message_names_the_checkout_time(db, hotel):
    check_in(db, hotel)
    reminders.send_due(db, hotel, at=T(16, 9, 5))

    assert "12:00" in sent_texts(db, hotel)[0]


def test_it_lands_in_the_thread_of_that_stay_not_that_room(db, hotel):
    """تذكيرٌ يهبط في محادثة من غادر يقرؤه النزيل الجديد في نفس الغرفة."""
    from daif.models import Guest

    stay = check_in(db, hotel)
    reminders.send_due(db, hotel, at=T(16, 9, 5))

    guest = db.scalar(select(Guest).where(Guest.wa_id == f"web:{stay.id}"))
    assert guest is not None
    assert db.scalar(select(Message).where(Message.guest_id == guest.id)) is not None


def test_a_departed_guest_is_not_reminded_to_depart(db, hotel):
    stay = check_in(db, hotel)
    stays.close_stay(db, stay)
    db.flush()

    assert reminders.send_due(db, hotel, at=T(16, 9, 5)) == []


def test_each_stay_gets_its_own(db, hotel):
    check_in(db, hotel, room="402")
    check_in(db, hotel, room="403")

    assert len(reminders.send_due(db, hotel, at=T(16, 9, 5))) == 2


# --- المجدول -------------------------------------------------------------------


def test_the_scheduler_reminds_and_folds_in_one_pass(db, hotel):
    """كان `expire_due` مكتوبًا ومختبَرًا ولا يناديه شيء في الإنتاج."""
    from daif import tick

    check_in(db, hotel, room="402")
    check_in(db, hotel, room="601", out_day=14)   # انتهت قبل يومين ولم تُطوَ

    report = tick.run_once(db, at=T(16, 9, 5))
    assert report.expired == 1
    assert report.reminded == 1
    assert report.tenants == 1


def test_one_broken_hotel_does_not_stop_the_others(db, hotel, monkeypatch):
    from daif import tick

    other = Tenant(slug="anwar", name="فندق الأنوار")
    db.add(other)
    db.flush()
    check_in(db, other, room="101")
    # نثبّت التجهيزة: `run_once` يتراجع عن معاملة الفندق المتعثّر، فيمحو
    # معها كل ما لم يُثبَّت — ومنه فنادق الاختبار الأخرى.
    db.commit()

    real = reminders.send_due

    def explode(session, tenant, at=None):
        if tenant.slug == "taibah":
            raise RuntimeError("عطل مفتعل")
        return real(session, tenant, at=at)

    monkeypatch.setattr(tick.reminders, "send_due", explode)
    report = tick.run_once(db, at=T(16, 9, 5))

    assert report.errors and "taibah" in report.errors[0]
    assert report.reminded == 1, "عطل فندق أوقف بقية الفنادق"
