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


# --- البحث في اللوحة --------------------------------------------------------

@pytest.fixture
def searchable(db):
    from datetime import date, timedelta

    from daif.models import Fact, Tenant

    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    soon = date.today() + timedelta(days=5)
    db.add_all([
        Fact(tenant_id=tenant.id, key="K01", topic="wifi",
             text="الواي فاي: الشبكة Taibah-Guest", seasons="normal,ramadan,hajj"),
        Fact(tenant_id=tenant.id, key="K02", topic="breakfast",
             text="الإفطار من ٥:٣٠", seasons="normal,hajj"),
        Fact(tenant_id=tenant.id, key="K03", topic="suhoor",
             text="السحور من ١:٣٠", seasons="ramadan"),
        Fact(tenant_id=tenant.id, key="K04", topic="laundry",
             text="غسيل الملابس", seasons="normal", paid=True),
        Fact(tenant_id=tenant.id, key="K05", topic="pool",
             text="المسبح مغلق", seasons="normal", valid_until=soon),
        Fact(tenant_id=tenant.id, key="K06", topic="old",
             text="حقيقة معطّلة", seasons="normal", active=False),
    ])
    db.flush()
    return tenant


def keys(rows) -> list[str]:
    return [f.key for f in rows]


def test_search_matches_text_topic_and_id(searchable, db):
    from daif.repository import search_facts

    assert keys(search_facts(db, searchable.id, query="الواي فاي")) == ["K01"]
    assert keys(search_facts(db, searchable.id, query="breakfast")) == ["K02"]  # من الموضوع
    assert keys(search_facts(db, searchable.id, query="K03")) == ["K03"]


def test_season_filter_matches_whole_names_only(searchable, db):
    """المواسم مخزّنة مفصولة بفواصل — «normal» يجب ألا تلتقط شيئًا آخر."""
    from daif.repository import search_facts

    assert keys(search_facts(db, searchable.id, season="ramadan")) == ["K01", "K03"]
    assert "K03" not in keys(search_facts(db, searchable.id, season="normal"))
    assert keys(search_facts(db, searchable.id, season="hajj")) == ["K01", "K02"]


def test_status_filters(searchable, db):
    from daif.repository import search_facts

    assert "K06" not in keys(search_facts(db, searchable.id, status="active"))
    assert keys(search_facts(db, searchable.id, status="inactive")) == ["K06"]
    assert keys(search_facts(db, searchable.id, status="paid")) == ["K04"]
    assert keys(search_facts(db, searchable.id, status="expiring")) == ["K05"]


def test_filters_combine(searchable, db):
    from daif.repository import search_facts

    assert keys(search_facts(db, searchable.id, query="ال", season="ramadan")) == ["K01", "K03"]


def test_empty_query_returns_everything(searchable, db):
    from daif.repository import search_facts

    assert len(search_facts(db, searchable.id)) == 6
    assert len(search_facts(db, searchable.id, query="   ")) == 6


def test_search_is_scoped_to_one_hotel(searchable, db):
    from daif.models import Fact, Tenant
    from daif.repository import search_facts

    other = Tenant(slug="anwar", name="الأنوار")
    db.add(other)
    db.flush()
    db.add(Fact(tenant_id=other.id, key="K01", topic="wifi", text="الواي فاي: Anwar"))
    db.flush()
    assert keys(search_facts(db, searchable.id, query="الواي فاي")) == ["K01"]
    assert search_facts(db, searchable.id, query="Anwar") == []
