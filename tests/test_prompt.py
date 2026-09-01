"""اختبارات تركيب البرومبت وفصل البادئة المخزّنة مؤقتًا."""

from __future__ import annotations

from daif.prompt import build_cached_system, build_inline_system, render_operating_context


def test_no_placeholder_survives(ctx):
    text = build_cached_system("فندق طيبة", "K01 | حقيقة")
    assert "{{" not in text and "}}" not in text


def test_cached_prefix_excludes_volatile_context(ctx):
    """البادئة المخزّنة يجب ألا تحمل كتلة السياق الحيّة، وإلا بطل التخزين المؤقت.

    ملاحظة: لا يصح البحث عن «402» أو «11:15» في النص، فهما واردان أصلًا في
    أمثلة المواصفة الحرفية. الفاصل الحقيقي هو ترويسة كتلة السياق نفسها.
    """
    text = build_cached_system("فندق طيبة", "K01 | حقيقة")
    assert "Now (Riyadh time):" not in text
    assert "Housekeeping window:" not in text
    assert "operator message" in text
    # النسخة الكاملة بالمقابل تحمل الكتلة
    assert "Now (Riyadh time):" in build_inline_system("فندق طيبة", "K01 | حقيقة", ctx)


def test_cached_prefix_is_identical_across_guests_and_times(ctx):
    from dataclasses import replace
    from datetime import timedelta

    other = replace(ctx, room="915", guest_name="فاطمة", now=ctx.now + timedelta(hours=3))
    facts = "K01 | حقيقة"
    assert build_cached_system(ctx.hotel_name, facts) == build_cached_system(other.hotel_name, facts)


def test_operating_context_carries_every_field(ctx):
    block = render_operating_context(ctx)
    for expected in ["402", "أحمد", "normal", "08:00-16:00", "staffed", "individual"]:
        assert expected in block


def test_unverified_room_is_explicit(ctx):
    from dataclasses import replace

    block = render_operating_context(replace(ctx, room=""))
    assert "unverified" in block


def test_inline_fallback_contains_context(ctx):
    text = build_inline_system("فندق طيبة", "K01 | حقيقة", ctx)
    assert "402" in text
    assert "{{" not in text
