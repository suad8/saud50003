"""الجواب الفوري من قاعدة المعرفة — بلا نموذج، وبلا مفتاح، وبلا انتظار.

أكثر أسئلة النزلاء عشرة أسئلة جوابها مكتوب حرفيًا عندنا. والنموذج لا يفعل
بها أكثر من إعادة ما أعطيناه إيّاه — بثانيتين وتكلفة.

وحدود المطابقة هي موضوع أكثر هذي الاختبارات: جوابٌ واثق وخاطئ أسوأ من
تحويلٍ لموظف.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from daif import local_answer as la
from daif.assistant import Assistant
from daif.clock import RIYADH
from daif.context import GuestContext


def at(season="normal", hour=11):
    return GuestContext(
        hotel_name="فندق طيبة", now=datetime(2026, 9, 30, hour, 15, tzinfo=RIYADH),
        season=season, room="402", guest_name="أحمد", hk_window_raw="08:00-16:00",
        desk_status="staffed", group_mode="individual")


@pytest.fixture
def ask(kb):
    """يسأل بلا مفتاح نموذج إطلاقًا — فما يُجاب يأتي من قاعدة المعرفة وحدها."""
    assistant = Assistant()

    def go(message, season="normal"):
        return assistant.reply(ctx=at(season), kb=kb, message=message)

    return go


# --- يجيب فورًا -----------------------------------------------------------------


@pytest.mark.parametrize("question,expected", [
    ("وش كلمة سر الواي فاي؟", "K01"),
    ("الوايفاي وش كلمة سره", "K01"),
    ("متى الإفطار؟", "K02"),
    ("متى وقت الخروج؟", "K08"),
    ("وين ألقى ماء زمزم", "K14"),
    ("وين أركن سيارتي", "K16"),
    ("فين المصلى", "K15"),
])
def test_a_known_question_is_answered_from_the_knowledge_base(ask, question, expected):
    result = ask(question)

    assert result.model == "knowledge_base", "ذهب للنموذج بلا داعٍ"
    assert expected in result.reply.sources
    assert not result.degraded, "الاقتباس مسارٌ مقصود لا تنازل عن جودة"


def test_it_costs_nothing_and_waits_for_nothing(ask):
    result = ask("وش كلمة سر الواي فاي؟")

    assert result.usage.input_tokens == 0 and result.usage.output_tokens == 0
    assert result.latency_ms < 60


def test_the_answer_is_the_stored_text_verbatim(ask, kb):
    """اقتباس نصّ مخزَّن لا يمكن أن يخترع معلومة، لأنه لا يولّد شيئًا."""
    stored = next(f.text for f in kb.facts if f.id == "K01")
    assert ask("وش كلمة سر الواي فاي؟").reply.answer == stored


def test_two_complementary_facts_are_shown_together(ask):
    """باب الرجال وباب النساء متمّمان لا متعارضين — حجبهما أسوأ من عرضهما."""
    result = ask("أقرب باب للحرم")

    assert result.model == "knowledge_base"
    assert {"K06", "K07"} <= set(result.reply.sources)


# --- يمتنع حين يجب ---------------------------------------------------------------


@pytest.mark.parametrize("question", [
    "أبغى مناشف",            # طلب خدمة: يستحق تذكرة لا اقتباسًا
    "أبي تنظيف الغرفة",
    "المكيف ما يشتغل",       # شكوى
    "وش الفرق بين الغرف",    # مقارنة
    "كم الساعة",             # لا حقيقة تغطيه
    "السلام عليكم",
])
def test_what_needs_understanding_is_not_quoted(ask, question):
    assert ask(question).model != "knowledge_base"


def test_a_service_request_still_reaches_a_human(ask):
    """الاقتباس لو سبق الطلب لحرمه من تذكرته، فيظن النزيل أن طلبه سُجِّل."""
    result = ask("أبغى مناشف إضافية")

    assert result.reply.handoff is not None or result.reply.request is not None


def test_a_non_arabic_guest_goes_to_the_model(kb):
    """نصّ الحقائق عربي، ونزيلٌ كتب بالتركية يستحق جوابًا بلغته."""
    assert la.answer("wifi password", list(kb.facts), language="tr") is None


# --- الحواجز تبقى فوق الاقتباس ------------------------------------------------


def test_a_fact_hidden_by_the_season_is_never_quoted(ask):
    """في رمضان لا يوجد «إفطار الساعة ٥:٣٠» — الحقيقة محجوبة، فلا تُقتبس."""
    normal = ask("متى الإفطار؟", season="normal")
    ramadan = ask("متى الإفطار؟", season="ramadan")

    assert normal.model == "knowledge_base" and "K02" in normal.reply.sources
    assert ramadan.model != "knowledge_base", "اقتُبست حقيقة يحجبها الموسم"


def test_a_seasonal_fact_hidden_out_of_season_is_not_quoted(ask):
    assert ask("متى المسبح يفتح؟", season="normal").model == "knowledge_base"
    assert ask("متى المسبح يفتح؟", season="ramadan").model != "knowledge_base"


def test_a_restricted_topic_is_never_short_circuited(ask):
    """فحص المواضيع الممنوعة يشدّد — فلا يجوز أن يتجاوزه اختصار."""
    result = ask("عندي ألم في صدري وأبغى دواء، ووش كلمة سر الواي فاي")
    assert result.model != "knowledge_base"


# --- المطابقة نفسها ---------------------------------------------------------------


@pytest.mark.parametrize("written", [
    "الإفطار", "الافطار", "الإفـطار", "الافطار", "اﻹفطار",
])
def test_the_same_word_matches_however_it_is_written(written, kb):
    """همزات ومدّات وتطويل — صورة واحدة بعد التطبيع."""
    found = la.answer(f"متى {written}؟", list(kb.active(at().now, "normal")))
    assert found is not None and "K02" in found.fact_key


def test_an_ambiguous_question_defers_instead_of_guessing(kb):
    """كلمة تخدم موضوعين: النموذج يفهم السياق، والمطابقة لا."""
    facts = list(kb.active(at().now, "normal"))
    assert la.answer("الإفطار والعشاء وش أوقاتهم؟", facts) is None


def test_normalisation_folds_arabic_indic_digits():
    assert "5" in la.normalize("الغرفة ٥٠٢")
