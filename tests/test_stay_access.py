"""دخول النزيل عبر الملصق — وما يمنع من غادر من العودة.

كل اختبار هنا يمثّل طريقة واقعية لاختراق الباب، لا حالة سعيدة.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from daif import stay as S
from daif.clock import now_riyadh

# تاريخ اليوم بتوقيت الرياض — نفس ما يقيس به الكود، لا توقيت الخادم.
def today():
    return now_riyadh().date()
from daif.models import Tenant


@pytest.fixture
def hotel(db):
    t = Tenant(slug="taibah", name="فندق طيبة")
    db.add(t)
    db.flush()
    return t


def _enter(db, hotel, room, **kw):
    kw.setdefault("mode", "stay_code")
    return S.enter(db, hotel.id, room, **kw)


# --- المسار الطبيعي ---------------------------------------------------------

def test_scan_without_proof_asks_for_it(db, hotel):
    S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    got = _enter(db, hotel, "402")
    assert not got.granted
    assert got.challenge == "stay_code"


def test_correct_code_binds_the_device(db, hotel):
    st = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    got = _enter(db, hotel, "402", proof=st.stay_code)
    assert got.granted and got.token


def test_bound_device_returns_without_asking_again(db, hotel):
    st = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    token = _enter(db, hotel, "402", proof=st.stay_code).token
    again = _enter(db, hotel, "402", device_token=token)
    assert again.granted and not again.needs_proof


def test_code_is_case_insensitive(db, hotel):
    st = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    assert _enter(db, hotel, "402", proof=st.stay_code.lower()).granted


# --- المغادرة: جوهر المسألة -------------------------------------------------

def test_checkout_kills_the_session_immediately(db, hotel):
    """النزيل غادر — جهازه يسقط في نفس اللحظة، لا عند انتهاء المهلة."""
    st = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    token = _enter(db, hotel, "402", proof=st.stay_code).token
    assert S.check(db, hotel.id, "402", token).granted

    S.close_stay(db, st, by="pms")
    after = S.check(db, hotel.id, "402", token)
    assert not after.granted
    assert after.reason == "no_active_stay"


def test_old_guest_link_in_browser_history_is_dead(db, hotel):
    """أخطر حالة: نزيل غادر ورابط الغرفة باقٍ في سجل متصفحه.

    يعود بعد أسبوع والغرفة فيها نزيل جديد. جهازه القديم لا يدخل، وكود
    الإقامة الجديد لا يعرفه، وكوده القديم لا ينفع.
    """
    first = S.open_stay(db, hotel.id, "402", checkout_on=today())
    old_token = _enter(db, hotel, "402", proof=first.stay_code).token
    old_code = first.stay_code

    S.close_stay(db, first, by="pms")
    second = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=4))

    assert not S.check(db, hotel.id, "402", old_token).granted
    assert not _enter(db, hotel, "402", device_token=old_token).granted
    assert not _enter(db, hotel, "402", proof=old_code).granted
    assert _enter(db, hotel, "402", proof=second.stay_code).granted


def test_new_checkin_closes_the_previous_stay(db, hotel):
    """تسجيل وصول جديد وحده كافٍ لقطع من قبله، ولو نسي الفندق يقفل."""
    first = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=9))
    token = _enter(db, hotel, "402", proof=first.stay_code).token
    S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=2))
    assert not S.check(db, hotel.id, "402", token).granted


def test_forgotten_stay_expires_on_its_own(db, hotel):
    """لا تكامل ولا موظف — السقف المطلق يقفل الغرفة المنسية وحده."""
    st = S.open_stay(db, hotel.id, "402", checkout_on=today())
    token = _enter(db, hotel, "402", proof=st.stay_code).token
    later = st.expires_at + timedelta(minutes=1)
    assert not S.check(db, hotel.id, "402", token, at=later).granted
    assert S.expire_due(db, hotel.id, at=later) == 1


def test_grace_keeps_the_lobby_question_working(db, hotel):
    """قطع الخدمة عند منتصف النهار بالضبط قسوة بلا فائدة."""
    st = S.open_stay(db, hotel.id, "402", checkout_on=today())
    token = _enter(db, hotel, "402", proof=st.stay_code).token
    just_after_noon = st.expires_at - timedelta(hours=1)
    assert S.check(db, hotel.id, "402", token, at=just_after_noon).granted


def test_no_stay_means_no_door_at_all(db, hotel):
    """غرفة فاضية: الملصق موجود على الجدار ولا يفتح شيئًا."""
    got = _enter(db, hotel, "402", proof="ABC123")
    assert not got.granted
    assert got.reason == "no_active_stay"
    assert not got.needs_proof      # لا نسأل عن إثبات لباب غير قائم


# --- محاولات التخمين والانتحال ----------------------------------------------

def test_wrong_code_is_refused(db, hotel):
    S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    got = _enter(db, hotel, "402", proof="ZZZZZZ")
    assert not got.granted and got.reason == "proof_wrong"


def test_code_of_another_room_does_not_open_this_one(db, hotel):
    other = S.open_stay(db, hotel.id, "318", checkout_on=today() + timedelta(days=3))
    S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    assert not _enter(db, hotel, "402", proof=other.stay_code).granted


def test_device_of_another_room_does_not_open_this_one(db, hotel):
    a = S.open_stay(db, hotel.id, "318", checkout_on=today() + timedelta(days=3))
    S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    token = _enter(db, hotel, "318", proof=a.stay_code).token
    assert not S.check(db, hotel.id, "402", token).granted


def test_stay_of_another_hotel_does_not_open_this_one(db, hotel):
    """العزل بين الفنادق يسري هنا أيضًا."""
    rival = Tenant(slug="anwar", name="فندق الأنوار")
    db.add(rival)
    db.flush()
    theirs = S.open_stay(db, rival.id, "402", checkout_on=today() + timedelta(days=3))
    S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    token = _enter(db, hotel, "402", proof=theirs.stay_code)
    assert not token.granted


def test_device_cap_stops_a_shared_link(db, hotel):
    """رابط منشور في مجموعة واتساب: العائلة تمر، والحشد لا."""
    st = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    for _ in range(S.MAX_DEVICES):
        assert _enter(db, hotel, "402", proof=st.stay_code).granted
    over = _enter(db, hotel, "402", proof=st.stay_code)
    assert not over.granted and over.reason == "device_limit"


def test_raw_token_is_never_stored(db, hotel):
    """تسريب قاعدة البيانات لا يمنح أحدًا جلسة."""
    st = S.open_stay(db, hotel.id, "402", checkout_on=today() + timedelta(days=3))
    token = _enter(db, hotel, "402", proof=st.stay_code).token
    db.refresh(st)
    assert all(token not in d.token_hash for d in st.devices)


# --- وضع آخر أربعة أرقام ----------------------------------------------------

def test_phone_last4_mode(db, hotel):
    S.open_stay(db, hotel.id, "402", phone="966501234567", mode="phone_last4",
                checkout_on=today() + timedelta(days=3))
    assert not _enter(db, hotel, "402", mode="phone_last4", proof="1111").granted
    assert _enter(db, hotel, "402", mode="phone_last4", proof="4567").granted


def test_phone_last4_accepts_the_whole_number_too(db, hotel):
    """النزيل قد يكتب رقمه كاملًا. لا نعاقبه على ذلك."""
    S.open_stay(db, hotel.id, "402", phone="966501234567", mode="phone_last4",
                checkout_on=today() + timedelta(days=3))
    assert _enter(db, hotel, "402", mode="phone_last4", proof="0501234567").granted


def test_missing_phone_closes_the_door_not_opens_it(db, hotel):
    """الفندق اختار الوضع ولم يسجّل رقمًا. الإعداد الناقص يمنع، لا يسمح."""
    S.open_stay(db, hotel.id, "402", mode="phone_last4",
                checkout_on=today() + timedelta(days=3))
    got = _enter(db, hotel, "402", mode="phone_last4", proof="4567")
    assert not got.granted and got.reason == "not_configured"


# --- وضع تفعيل الاستقبال ----------------------------------------------------

def test_desk_arm_lets_the_first_scan_through(db, hotel):
    S.open_stay(db, hotel.id, "402", mode="desk_arm",
                checkout_on=today() + timedelta(days=3))
    assert _enter(db, hotel, "402", mode="desk_arm").granted


def test_desk_arm_window_closes(db, hotel):
    S.open_stay(db, hotel.id, "402", mode="desk_arm",
                checkout_on=today() + timedelta(days=3))
    late = now_riyadh() + S.ARM_WINDOW + timedelta(minutes=1)
    got = _enter(db, hotel, "402", mode="desk_arm", at=late)
    assert not got.granted and got.reason == "window_closed"


def test_desk_arm_still_dies_at_checkout(db, hotel):
    st = S.open_stay(db, hotel.id, "402", mode="desk_arm",
                     checkout_on=today() + timedelta(days=3))
    token = _enter(db, hotel, "402", mode="desk_arm").token
    S.close_stay(db, st, by="desk")
    assert not S.check(db, hotel.id, "402", token).granted


# --- السيناريو الذي طُلب هذا النظام من أجله --------------------------------

def test_the_whole_story_end_to_end(db, hotel):
    """نزيل يسكن، يستعمل الشات، يغادر، ثم يحاول العودة بكل ما يملك.

    هذا ليس اختبار وحدة — هو محضر الحالة التي بُني الملف كله لأجلها.
    """
    # يوم ١: أحمد يسجّل وصوله ويمسح الملصق
    ahmad = S.open_stay(db, hotel.id, "402", guest_name="أحمد",
                        phone="966500000001", checkout_on=today() + timedelta(days=2))
    ahmad_phone = _enter(db, hotel, "402", proof=ahmad.stay_code)
    assert ahmad_phone.granted

    # زوجته تمسح من جوالها — العائلة مسموح لها
    wife = _enter(db, hotel, "402", proof=ahmad.stay_code)
    assert wife.granted

    # يوم ٣: يغادران
    S.close_stay(db, ahmad, by="pms")
    assert not S.check(db, hotel.id, "402", ahmad_phone.token).granted
    assert not S.check(db, hotel.id, "402", wife.token).granted

    # الغرفة فاضية: الملصق على الجدار لا يفتح شيئًا لأحد
    assert not _enter(db, hotel, "402", proof=ahmad.stay_code).granted

    # يوم ٥: سارة تسكن نفس الغرفة
    sarah = S.open_stay(db, hotel.id, "402", guest_name="سارة",
                        phone="966500009999", checkout_on=today() + timedelta(days=3))

    # أحمد يفتح الرابط من سجل متصفحه ويجرّب كل ما عنده — ولا شيء يمرّ
    assert not _enter(db, hotel, "402", device_token=ahmad_phone.token).granted
    assert not _enter(db, hotel, "402", proof=ahmad.stay_code).granted
    assert not _enter(db, hotel, "402", proof="").granted

    # سارة تمسح بكودها المطبوع وتدخل
    assert _enter(db, hotel, "402", proof=sarah.stay_code).granted
