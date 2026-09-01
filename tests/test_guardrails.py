"""اختبارات الحواجز — القواعد المطلقة مطبّقة برمجيًا لا في البرومبت فقط.

المبدأ المُختبَر في كل حالة: الحواجز تشدّد فقط، ولا تحوّل تحويلًا إلى جواب.
"""

from __future__ import annotations

from dataclasses import replace

from daif.guardrails import enforce, split_sentences, trim_sentences
from daif.restricted import screen
from daif.schema import GuestReply, Handoff, ServiceRequest


def reply(**overrides) -> GuestReply:
    """رد صالح افتراضيًا، تُعدَّل حقوله في كل اختبار."""
    base = dict(
        intent="inquiry",
        in_scope=True,
        language="ar",
        answer="الشبكة Taibah-Guest وكلمة السر welcome2026.",
        sources=["K01"],
        request=None,
        handoff=None,
        confidence=0.95,
    )
    base.update(overrides)
    return GuestReply(**base)


def ticket(**overrides) -> ServiceRequest:
    base = dict(
        type="cleaning", room="402", detail="طلب تنظيف", requested_time=None, urgency="normal"
    )
    base.update(overrides)
    return ServiceRequest(**base)


# --- المصادر ---------------------------------------------------------------

def test_valid_reply_passes_untouched(ctx, kb):
    out = enforce(reply(), ctx, kb)
    assert out.clean
    assert out.reply.in_scope is True
    assert out.reply.sources == ["K01"]


def test_invented_source_id_forces_handoff(ctx, kb):
    """معرّف غير موجود = معلومة مخترعة. لا يُنشر على النزيل."""
    out = enforce(reply(sources=["K99"]), ctx, kb)
    assert out.reply.in_scope is False
    assert out.reply.handoff.reason == "no_documented_answer"
    assert any("غير موجود" in v for v in out.violations)


def test_out_of_season_source_is_stripped(ctx, kb):
    """K02 (الإفطار العادي) غير صالح في رمضان — الاستشهاد به يسقط."""
    ramadan = replace(ctx, season="ramadan")
    out = enforce(reply(sources=["K02"]), ramadan, kb)
    assert out.reply.in_scope is False
    assert out.reply.handoff is not None


def test_in_scope_answer_without_source_is_rejected(ctx, kb):
    out = enforce(reply(sources=[]), ctx, kb)
    assert out.reply.in_scope is False
    assert out.reply.handoff.reason == "no_documented_answer"


def test_service_request_may_have_no_source(ctx, kb):
    """التذاكر لا تحتاج استشهادًا — القاعدة تخصّ الأجوبة."""
    out = enforce(
        reply(intent="request", sources=[], answer="أُبلغ التدبير الفندقي.", request=ticket()),
        ctx,
        kb,
    )
    assert out.reply.in_scope is True
    assert out.reply.request is not None


# --- الغرفة ----------------------------------------------------------------

def test_request_for_other_room_is_refused(ctx, kb):
    """القاعدة ٥: لا تُفتح تذكرة لغرفة كتبها النزيل في نص الرسالة."""
    out = enforce(reply(intent="request", request=ticket(room="511"), sources=[]), ctx, kb)
    assert out.reply.request is None
    assert out.reply.handoff.reason == "unverified_room"


def test_request_without_verified_room_is_refused(ctx, kb):
    anonymous = replace(ctx, room="")
    out = enforce(
        reply(intent="request", request=ticket(room="402"), sources=[]), anonymous, kb
    )
    assert out.reply.request is None
    assert out.reply.handoff.reason == "unverified_room"


# --- الشكاوى ---------------------------------------------------------------

def test_complaint_goes_to_duty_manager_and_drops_ticket(ctx, kb):
    out = enforce(
        reply(
            intent="complaint",
            in_scope=True,
            answer="أعتذر، سنصلحه.",
            sources=[],
            request=ticket(type="maintenance"),
        ),
        ctx,
        kb,
    )
    assert out.reply.request is None
    assert out.reply.in_scope is False
    assert out.reply.handoff.to == "duty_manager"
    assert out.reply.handoff.reason == "complaint"


# --- المواضيع الممنوعة ------------------------------------------------------

def test_restricted_topic_overrides_a_confident_answer(ctx, kb):
    """حتى لو أجاب النموذج بثقة عن وقت الصلاة، الحاجز يحوّله."""
    match = screen("متى صلاة الفجر؟")
    out = enforce(
        reply(answer="الفجر الساعة ٤:٣٠.", sources=["K01"], confidence=0.99),
        ctx,
        kb,
        restricted=match,
    )
    assert out.reply.in_scope is False
    assert out.reply.handoff.reason == "restricted_topic"
    assert out.reply.sources == []


def test_restricted_complaint_still_reaches_duty_manager(ctx, kb):
    match = screen("فاتورتي غلط وأبي المدير")
    out = enforce(
        reply(intent="complaint", in_scope=False, sources=[], answer="…"),
        ctx,
        kb,
        restricted=match,
    )
    assert out.reply.handoff.to == "duty_manager"


# --- الثقة -----------------------------------------------------------------

def test_low_confidence_forces_handoff(ctx, kb):
    out = enforce(reply(confidence=0.55), ctx, kb)
    assert out.reply.in_scope is False
    assert out.reply.handoff is not None


def test_confidence_out_of_range_is_clamped(ctx, kb):
    out = enforce(reply(confidence=4.2), ctx, kb)
    assert 0.0 <= out.reply.confidence <= 1.0


# --- الوقت -----------------------------------------------------------------

def test_requested_time_outside_housekeeping_window_is_dropped(ctx, kb):
    out = enforce(
        reply(
            intent="request",
            sources=[],
            request=ticket(requested_time="2026-09-01T17:00:00+03:00"),
        ),
        ctx,
        kb,
    )
    assert out.reply.request.requested_time is None
    assert "أُلغي الوقت المطلوب" in out.reply.request.detail


def test_requested_time_inside_window_is_kept(ctx, kb):
    out = enforce(
        reply(
            intent="request",
            sources=[],
            request=ticket(requested_time="2026-09-01T15:30:00+03:00"),
        ),
        ctx,
        kb,
    )
    assert out.reply.request.requested_time == "2026-09-01T15:30:00+03:00"


def test_past_time_is_dropped(ctx, kb):
    out = enforce(
        reply(
            intent="request",
            sources=[],
            request=ticket(requested_time="2026-09-01T09:00:00+03:00"),
        ),
        ctx,
        kb,
    )
    assert out.reply.request.requested_time is None


# --- الاتساق ---------------------------------------------------------------

def test_handoff_and_ticket_cannot_coexist(ctx, kb):
    out = enforce(
        reply(
            intent="request",
            sources=[],
            request=ticket(),
            handoff=Handoff(reason="out_of_hours", to="housekeeping", note="خارج الدوام"),
        ),
        ctx,
        kb,
    )
    assert out.reply.request is None
    assert out.reply.handoff is not None


def test_out_of_scope_without_handoff_gets_one(ctx, kb):
    out = enforce(reply(in_scope=False, sources=[], handoff=None), ctx, kb)
    assert out.reply.handoff is not None


def test_language_name_is_normalised_not_rejected(ctx, kb):
    """رمز لغة خاطئ لا يستحق إحالة نزيل — يُطبَّع ما دام مفهومًا."""
    out = enforce(reply(language="Arabic"), ctx, kb)
    assert out.reply.language == "ar"
    assert out.reply.in_scope is True


def test_language_tag_is_reduced_to_iso_code(ctx, kb):
    out = enforce(reply(language="ar-SA"), ctx, kb)
    assert out.reply.language == "ar"


def test_unreadable_language_code_forces_handoff(ctx, kb):
    out = enforce(reply(language="???"), ctx, kb)
    assert out.reply.in_scope is False
    assert out.reply.handoff.reason == "low_confidence"


def test_empty_answer_forces_handoff(ctx, kb):
    out = enforce(reply(answer="   "), ctx, kb)
    assert out.reply.handoff is not None


# --- الطول والتصعيد ---------------------------------------------------------

def test_answer_longer_than_three_sentences_is_trimmed(ctx, kb):
    long_answer = "واحد. اثنان. ثلاثة. أربعة. خمسة."
    out = enforce(reply(answer=long_answer), ctx, kb)
    assert len(split_sentences(out.reply.answer)) == 3
    assert any("قُصّ" in v for v in out.violations)


def test_urgent_request_escalates_when_desk_unstaffed(ctx, kb):
    night = replace(ctx, desk_status="unstaffed")
    out = enforce(
        reply(intent="request", sources=[], request=ticket(type="maintenance", urgency="urgent")),
        night,
        kb,
    )
    assert out.escalate is True


def test_urgent_request_does_not_escalate_when_staffed(ctx, kb):
    out = enforce(
        reply(intent="request", sources=[], request=ticket(type="maintenance", urgency="urgent")),
        ctx,
        kb,
    )
    assert out.escalate is False


# --- تقسيم الجمل ------------------------------------------------------------

def test_decimals_are_not_sentence_breaks():
    assert split_sentences("السعر 2.5 ريال فقط.") == ["السعر 2.5 ريال فقط."]


def test_trim_keeps_short_answers_intact():
    text = "جملة واحدة فقط."
    assert trim_sentences(text, 3) == (text, False)
