"""اختبارات الفحص المسبق للمواضيع الممنوعة."""

from __future__ import annotations

import pytest

from daif.restricted import screen

FLAGGED = [
    ("متى صلاة الفجر؟", "prayer_times"),
    ("When is fajr?", "prayer_times"),
    ("Waktu solat subuh jam berapa?", "prayer_times"),
    ("نماز کا وقت کیا ہے؟", "prayer_times"),
    ("Ezan saat kaçta?", "prayer_times"),
    ("هل يجوز الجمع بين الصلاتين؟", "worship"),
    ("كيف أحجز تصريح الروضة؟", "permits"),
    ("How do I book the Rawdah?", "permits"),
    ("عندي ألم في صدري", "medical"),
    ("I need medicine for fever", "medical"),
    ("حرارتي مرتفعة", "medical"),
    ("المفتاح ما يشتغل", "room_access"),
    ("my key card doesn't work", "room_access"),
    ("ابغى فاتورتي", "money"),
    ("Can I get a refund?", "money"),
    ("تأشيرتي منتهية", "government"),
    ("my visa expired", "government"),
    ("فيه حريق في الدور الثالث", "safety"),
    ("سرقوا جوالي", "safety"),
]

# حالات يجب ألا تُلتقط — إيجابية كاذبة تعني موظفًا يرد بلا داعٍ
ALLOWED = [
    "وش كلمة سر الواي فاي؟",
    "Is breakfast still open?",
    "ممكن حد يحمل الشنطة؟",       # «يحمل» ليست «حمل»
    "نظفوا الغرفة الساعة ٥",
    "كم سعر غسيل الملابس؟",        # سعر خدمة موثّقة، لا فاتورة
    "وين مطعم الروضة؟",            # اسم مطعم بلا قرينة تصريح
    "Pintu mana yang paling dekat ke Masjid Nabawi?",
    "أبغى مناشف إضافية",
    "متى الغداء؟",
    "أبغى تأخير الخروج",
    "الواي فاي ما يشتغل",
    "عمري ٧٠ سنة وأبغى غرفة قريبة من المصعد",
]


@pytest.mark.parametrize("message,category", FLAGGED)
def test_restricted_is_caught(message, category):
    match = screen(message)
    assert match is not None, f"فات موضوع ممنوع: {message}"
    assert match.category == category


@pytest.mark.parametrize("message", ALLOWED)
def test_ordinary_message_passes(message):
    assert screen(message) is None, f"إيجابية كاذبة على: {message}"


def test_empty_message():
    assert screen("") is None
    assert screen("   ") is None
