"""شكل النداء المرسَل للنموذج — ما لم يفحصه اختبار فسقط في الإنتاج وحده.

كل الاختبارات كانت تمرّر عميلًا وهميًا يقبل أي شيء، فما فحص أحدٌ ما يُرسل
فعلًا. وكان يُرسل دور «system» داخل `messages`، وواجهة الرسائل لا تقبل هناك
إلا user وassistant — فتردّ ٤٠٠ على كل رسالة، ويعمل النظام كله في الوضع
الاحتياطي: كل سؤال يتحوّل لموظف، والمساعد لا يجيب أبدًا.

ولم يظهر إلا على نشرٍ فيه مفتاح حقيقي. هذي الاختبارات تنقل الفحص إلى هنا.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from daif.assistant import Assistant, _reason
from daif.clock import RIYADH
from daif.schema import GuestReply

ALLOWED_ROLES = {"user", "assistant"}


def _reply() -> GuestReply:
    return GuestReply(intent="inquiry", in_scope=True, language="ar",
                      answer="الواي فاي: Taibah-Guest", sources=["K01"],
                      request=None, handoff=None, confidence=0.9)


# سؤالٌ لا تغطيه قاعدة المعرفة، فيصل النموذج فعلًا. «وش كلمة سر الواي فاي»
# صارت تُجاب اقتباسًا بلا نداء — وهو المقصود، لكنه يُفرغ هذي الاختبارات.
NEEDS_MODEL = "أبغى مناشف إضافية ووسادة لو سمحت"


@pytest.fixture
def sent(fake_reply, ctx, kb):
    """يشغّل المساعد ويعيد ما وصل العميل من وسائط."""
    client = fake_reply(_reply())
    Assistant(client=client).reply(ctx=ctx, kb=kb, message=NEEDS_MODEL)
    assert client.messages.calls, "لم يُستدعَ النموذج — السؤال أُجيب اقتباسًا"
    return client.messages.calls[-1]


def test_no_role_outside_user_and_assistant(sent):
    """دور «system» داخل messages يُرفض بـ٤٠٠ — في كل رسالة، بلا استثناء."""
    roles = [m["role"] for m in sent["messages"]]
    assert roles, "لم تُرسل رسائل"
    assert set(roles) <= ALLOWED_ROLES, f"دور غير مقبول: {set(roles) - ALLOWED_ROLES}"


def test_the_operating_context_still_reaches_the_model(sent):
    """نقله من system إلى user لا يجوز أن يُسقطه: بلاه لا يعرف الوقت ولا الغرفة."""
    joined = "\n".join(m["content"] for m in sent["messages"])
    assert "<operating_context>" in joined
    assert "Guest room:" in joined


def test_the_guest_message_is_the_last_turn(sent):
    """السياق يسبق السؤال، فالسؤال هو آخر ما يقرؤه النموذج."""
    assert sent["messages"][-1]["content"] == NEEDS_MODEL


def test_the_cached_prefix_carries_no_per_request_context(sent):
    """حشو الوقت والغرفة في البادئة يُبطل تخزينها مع كل رسالة."""
    system = sent["system"]
    text = system[0]["text"] if isinstance(system, list) else str(system)
    assert "Now (Riyadh time):" not in text
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_a_different_room_does_not_disturb_the_cached_prefix(fake_reply, ctx, kb):
    """جوهر التخزين المؤقت: يتغيّر المتغيّر وحده.

    الوقت يُستثنى هنا عمدًا — كتلة الحقائق تُرشَّح بالساعة والموسم، فتغيّرها
    مع الوقت سلوكٌ مقصود. أما الغرفة فلا أثر لها في البادئة أصلًا.
    """
    from dataclasses import replace

    client = fake_reply(_reply(), _reply())
    assistant = Assistant(client=client)
    assistant.reply(ctx=ctx, kb=kb, message=NEEDS_MODEL)
    assistant.reply(ctx=replace(ctx, room="511", guest_name="سارة"), kb=kb,
                    message=NEEDS_MODEL)

    first, second = client.messages.calls
    assert first["system"][0]["text"] == second["system"][0]["text"]
    assert "511" in second["messages"][0]["content"]
    assert "511" not in first["messages"][0]["content"]


# --- سبب العطل يُسجَّل، لا اسم صنفه وحده ---


def test_the_failure_reason_carries_the_message(ctx, kb):
    """«TypeError» بلا رسالة كلّف جولات تشخيص طويلة."""
    class Boom:
        class messages:
            @staticmethod
            def parse(**_kwargs):
                raise TypeError("messages.1.role: Input should be 'user' or 'assistant'")

    result = Assistant(client=Boom()).reply(ctx=ctx, kb=kb, message=NEEDS_MODEL)

    assert result.degraded
    detail = result.violations[0]
    assert "TypeError" in detail
    assert "messages.1.role" in detail, "الرسالة ضاعت، وبقي اسم الصنف وحده"


def test_a_very_long_reason_is_trimmed():
    assert len(_reason(ValueError("x" * 5000))) < 260


def test_a_reason_with_no_message_is_still_readable():
    assert _reason(RuntimeError()) == "RuntimeError"
