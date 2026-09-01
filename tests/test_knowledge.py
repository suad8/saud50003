"""اختبارات قاعدة المعرفة: الموسم، الصلاحية، وحالة الوقت."""

from __future__ import annotations

from datetime import datetime

import pytest

from daif.clock import RIYADH
from daif.knowledge import KnowledgeBase, KnowledgeError


def test_ramadan_hides_normal_breakfast(kb, now):
    """في رمضان لا يرى النموذج حقيقة الإفطار العادي أصلًا — لا يمكنه اقتباسها."""
    normal = kb.active_ids(now, "normal")
    ramadan = kb.active_ids(now, "ramadan")
    assert "K02" in normal
    assert "K02" not in ramadan
    assert "K04" in ramadan  # السحور
    assert "K04" not in normal


def test_expired_fact_disappears(now):
    kb = KnowledgeBase.from_records(
        [{"id": "K90", "text": "المسبح مغلق للصيانة.", "valid_until": "2026-08-01"}]
    )
    assert kb.active_ids(now, "normal") == frozenset()
    assert "K90" in kb.ids  # موجودة، لكنها غير صالحة


def test_future_fact_hidden_until_start(now):
    kb = KnowledgeBase.from_records(
        [{"id": "K91", "text": "مطعم جديد.", "valid_from": "2026-10-01"}]
    )
    assert kb.active_ids(now, "normal") == frozenset()


def test_status_hint_is_computed_not_guessed(kb):
    fact = kb.get("K02")
    open_at = datetime(2026, 9, 1, 7, 0, tzinfo=RIYADH)
    closed_at = datetime(2026, 9, 1, 11, 15, tzinfo=RIYADH)
    assert "مفتوح الآن" in fact.status_hint(open_at)
    assert "مغلق الآن" in fact.status_hint(closed_at)


def test_duplicate_ids_rejected():
    with pytest.raises(KnowledgeError):
        KnowledgeBase.from_records(
            [{"id": "K01", "text": "أ"}, {"id": "K01", "text": "ب"}]
        )


def test_bad_hours_rejected_at_load():
    with pytest.raises(KnowledgeError):
        KnowledgeBase.from_records([{"id": "K01", "text": "أ", "hours": "25:00-30:00"}])


def test_unknown_season_rejected():
    with pytest.raises(KnowledgeError):
        KnowledgeBase.from_records([{"id": "K01", "text": "أ", "season": "winter"}])
