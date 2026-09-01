"""أمثلة المواصفة الثمانية كاختبار انحدار.

هذه الأمثلة هي العقد المتفق عليه. أي حاجز يغيّر مخرجاتها يكون قد شدّد أكثر
مما ينبغي — والاختبار يمسك ذلك.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from daif.guardrails import enforce
from daif.restricted import screen
from daif.schema import GuestReply


def run(reply_json: dict, ctx, kb, guest_message: str):
    reply = GuestReply(**reply_json)
    return enforce(reply, ctx, kb, restricted=screen(guest_message))


def test_wifi_password(ctx, kb):
    out = run(
        {"intent": "inquiry", "in_scope": True, "language": "ar",
         "answer": "الشبكة Taibah-Guest وكلمة السر welcome2026.",
         "sources": ["K01"], "request": None, "handoff": None, "confidence": 0.97},
        ctx, kb, "وش كلمة سر الواي فاي؟",
    )
    assert out.clean
    assert out.reply.in_scope and out.reply.sources == ["K01"]


def test_breakfast_closed_at_1115(ctx, kb):
    out = run(
        {"intent": "inquiry", "in_scope": True, "language": "en",
         "answer": "Breakfast closed at 10:30 this morning. It runs from 5:30 to 10:30 in Al-Rawdah restaurant on the first floor.",
         "sources": ["K02"], "request": None, "handoff": None, "confidence": 0.95},
        ctx, kb, "Is breakfast still open?",
    )
    assert out.clean
    assert out.reply.in_scope


def test_nearest_gate_in_indonesian(ctx, kb):
    out = run(
        {"intent": "inquiry", "in_scope": True, "language": "id",
         "answer": "Gerbang King Fahd nomor 21 (باب الملك فهد), sekitar 7 menit berjalan kaki. Keluar hotel, belok kanan, lalu lurus.",
         "sources": ["K06"], "request": None, "handoff": None, "confidence": 0.93},
        ctx, kb, "Pintu mana yang paling dekat ke Masjid Nabawi?",
    )
    assert out.clean
    assert out.reply.language == "id"


def test_cleaning_outside_window_offers_alternative(ctx, kb):
    out = run(
        {"intent": "request", "in_scope": True, "language": "ar",
         "answer": "التنظيف ينتهي الساعة ٤ عصرًا. أقرب وقت متاح ٣:٣٠، أو غدًا صباحًا.",
         "sources": [],
         "request": {"type": "cleaning", "room": "402",
                     "detail": "طلب تنظيف — النزيل طلب ٥ مساءً، عُرض ٣:٣٠",
                     "requested_time": None, "urgency": "normal"},
         "handoff": None, "confidence": 0.9},
        ctx, kb, "نظفوا الغرفة الساعة ٥",
    )
    assert out.clean
    assert out.reply.request.room == "402"
    assert out.reply.request.requested_time is None


def test_cleaning_from_unverified_number(ctx, kb):
    anonymous = replace(ctx, room="")
    out = run(
        {"intent": "request", "in_scope": False, "language": "ar",
         "answer": "سيتواصل معك الاستقبال لتأكيد الطلب.",
         "sources": [], "request": None,
         "handoff": {"reason": "unverified_room", "to": "front_desk",
                     "note": "طلب تنظيف من رقم غير مربوط بغرفة — يحتاج تحقق"},
         "confidence": 0.99},
        anonymous, kb, "نظفوا غرفة ٤٠٢",
    )
    assert out.clean
    assert out.reply.handoff.reason == "unverified_room"


def test_prayer_time_is_refused(ctx, kb):
    out = run(
        {"intent": "out_of_scope", "in_scope": False, "language": "ar",
         "answer": "سيوافيك الاستقبال بذلك.",
         "sources": [], "request": None,
         "handoff": {"reason": "restricted_topic", "to": "front_desk",
                     "note": "سؤال عن وقت الصلاة — يُحال لموظف"},
         "confidence": 0.99},
        ctx, kb, "متى صلاة الفجر؟",
    )
    assert out.clean
    assert out.reply.handoff.reason == "restricted_topic"


def test_angry_complaint_goes_to_manager(ctx, kb):
    out = run(
        {"intent": "complaint", "in_scope": False, "language": "ar",
         "answer": "سيتواصل معك مدير الوردية الآن.",
         "sources": [], "request": None,
         "handoff": {"reason": "complaint", "to": "duty_manager",
                     "note": "شكوى مكرّرة — مكيف معطّل منذ أمس، النزيل يطلب المدير"},
         "confidence": 0.99},
        ctx, kb, "المكيف ما يشتغل من امس وقلت لكم مرتين!! ابي المدير",
    )
    assert out.clean
    assert out.reply.handoff.to == "duty_manager"


def test_undocumented_question_hands_off(ctx, kb):
    out = run(
        {"intent": "out_of_scope", "in_scope": False, "language": "ar",
         "answer": "سيوافيك الاستقبال بذلك.",
         "sources": [], "request": None,
         "handoff": {"reason": "no_documented_answer", "to": "front_desk",
                     "note": "سؤال خارج نطاق معلومات الفندق"},
         "confidence": 0.98},
        ctx, kb, "كم تبعد جدة عن المدينة؟",
    )
    assert out.clean
    assert out.reply.handoff.reason == "no_documented_answer"
