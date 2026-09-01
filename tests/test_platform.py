"""لوحة مشغّل المنصة وعزلها عن لوحات الفنادق."""

from __future__ import annotations

import pytest

from daif import billing
from daif.models import PlatformAdmin, Tenant
from daif.repository import list_tenants, platform_admin_by_email, platform_stats
from daif.security import hash_password
from daif.web import auth as tenant_auth
from daif.web import platform


@pytest.fixture
def platform_admin(db):
    admin = PlatformAdmin(
        email="ops@daif.sa", name="مشغّل", password_hash=hash_password("kalimat-mururr-tawila")
    )
    db.add(admin)
    db.flush()
    return admin


@pytest.fixture
def fleet(db):
    """أسطول مشتركين بحالات مختلفة."""
    hotels = [
        Tenant(slug="taibah", name="طيبة", plan="pro", rooms=250, active=True),
        Tenant(slug="anwar", name="الأنوار", plan="basic", rooms=90, active=True),
        Tenant(slug="noor", name="النور", plan="trial", rooms=40, active=True),
        Tenant(slug="old", name="قديم", plan="basic", rooms=60, active=False),
    ]
    db.add_all(hotels)
    db.flush()
    return hotels


# --- الجلسة ---------------------------------------------------------------

def test_platform_token_round_trips():
    token = platform.issue(7)
    assert platform.read(token) == 7


def test_forged_platform_token_rejected():
    assert platform.read("not.a.token") is None
    assert platform.read(None) is None


def test_tenant_token_is_not_valid_on_platform():
    """ملح توقيع مختلف: كوكي لوحة الفندق لا يفتح لوحة المنصة."""
    tenant_token = tenant_auth.issue(staff_id=1, tenant_id=1)
    assert platform.read(tenant_token) is None


def test_platform_token_is_not_valid_on_tenant_dashboard():
    assert tenant_auth.read(platform.issue(1)) is None


def test_admin_lookup_is_case_insensitive_and_active_only(db, platform_admin):
    assert platform_admin_by_email(db, "OPS@DAIF.SA") is not None
    platform_admin.active = False
    db.flush()
    assert platform_admin_by_email(db, "ops@daif.sa") is None


# --- مؤشرات المنصة ---------------------------------------------------------

def test_mrr_counts_paying_actives_only(db, fleet):
    from daif.plans import BASIC, PRO

    stats = platform_stats(db, "2026-09")
    # الاحترافية + الأساسية النشطتان فقط؛ التجربة بلا مقابل والمعلَّق مستبعَد
    assert stats["mrr"] == PRO.monthly + BASIC.monthly
    assert stats["active"] == 3
    assert stats["tenants"] == 4
    assert stats["trials"] == 1


def test_usage_is_summed_across_the_fleet(db, fleet):
    billing.record(db, fleet[0].id, inbound=100, outbound=90, period="2026-09")
    billing.record(db, fleet[1].id, inbound=50, outbound=40, period="2026-09")
    billing.record(db, fleet[1].id, inbound=999, outbound=999, period="2026-08")
    stats = platform_stats(db, "2026-09")
    assert (stats["inbound"], stats["outbound"]) == (150, 130)


def test_unpaid_invoices_are_counted(db, fleet):
    billing.issue_invoice(db, fleet[0], "2026-08")   # مدفوعة؟ لا — باقة مدفوعة
    billing.issue_invoice(db, fleet[2], "2026-08")   # تجربة — إجماليها صفر فتُعدّ مدفوعة
    stats = platform_stats(db, "2026-09")
    assert stats["unpaid_invoices"] == 1


def test_inactive_tenants_excluded_from_billing_run(db, fleet):
    active = list_tenants(db, include_inactive=False)
    assert {t.slug for t in active} == {"taibah", "anwar", "noor"}


def test_plan_breakdown(db, fleet):
    assert platform_stats(db, "2026-09")["by_plan"] == {"pro": 1, "basic": 1, "trial": 1}


# --- التهيئة ---------------------------------------------------------------

def test_seeded_knowledge_starts_disabled(db, fleet):
    """الحقائق النموذجية تُنشأ معطّلة: المدير يراجعها قبل أن ينطق بها المساعد."""
    from daif.repository import list_facts

    platform._seed_knowledge(db, fleet[0].id)
    db.flush()
    facts = list_facts(db, fleet[0].id)
    assert facts, "لم تُزرع أي حقيقة"
    assert all(not f.active for f in facts)
    # ولا تظهر للنموذج ما دامت معطّلة
    assert list_facts(db, fleet[0].id, only_active=True) == []
