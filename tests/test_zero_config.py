"""التشغيل بلا إعداد، ووضع العرض المعلَن."""

from __future__ import annotations

import os

import pytest

from daif import demo as demo_mod, secrets_store
from daif.models import PlatformSecret, Tenant


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("DAIF_SECRET_KEY", "DAIF_DASHBOARD_SECRET", "DAIF_ROOM_SIGNING_KEY"):
        monkeypatch.delenv(k, raising=False)
    secrets_store.reset_cache()
    yield
    secrets_store.reset_cache()


# --- الأسرار المولَّدة المحفوظة ---------------------------------------------

def test_secret_is_generated_when_nothing_is_configured(db):
    value = secrets_store.get("dashboard")
    assert value and len(value) >= 32


def test_the_same_secret_comes_back_after_a_restart(db):
    first = secrets_store.get("dashboard")
    secrets_store.reset_cache()          # كأن العملية أُعيد تشغيلها
    assert secrets_store.get("dashboard") == first


def test_two_secrets_are_not_the_same(db):
    assert secrets_store.get("dashboard") != secrets_store.get("roomqr")


def test_environment_wins_over_the_stored_value(db, monkeypatch):
    stored = secrets_store.get("dashboard")
    secrets_store.reset_cache()
    monkeypatch.setenv("DAIF_DASHBOARD_SECRET", "x" * 40)
    assert secrets_store.get("dashboard") == "x" * 40 != stored


def test_the_encryption_key_is_never_stored(db):
    """مفتاح تشفير أسرار الفنادق لا يُحفظ في القاعدة التي يحميها."""
    assert "DAIF_SECRET_KEY" not in secrets_store.MANAGED.values()


def test_the_hotel_link_needs_no_configuration(db):
    """الرابط الموحّد لا يحمل سرًّا، فلا شيء يُضبط قبل طباعته.

    كان هنا توقيعٌ لكل غرفة مشتقٌّ من سرّ الفندق، وتغييره يُبطل كل ما طُبع.
    زال الاثنان: الرابط ثابت، والإثبات انتقل إلى جوال النزيل.
    """
    from daif import roomqr

    assert roomqr.hotel_path("taibah") == "/h/taibah"
    assert roomqr.hotel_url("https://daif.sa/", "taibah") == "https://daif.sa/h/taibah"


def test_the_printed_code_survives_a_restart(db):
    from daif import roomqr

    before = roomqr.qr_svg(roomqr.hotel_url("https://daif.sa", "taibah"))
    secrets_store.reset_cache()
    after = roomqr.qr_svg(roomqr.hotel_url("https://daif.sa", "taibah"))
    assert before == after, "الملصق المطبوع بطل بعد إعادة التشغيل"


# --- وضع العرض ---------------------------------------------------------------

def test_banner_is_off_by_default(db):
    assert demo_mod.banner(db) is None


def test_banner_carries_both_logins(db):
    demo_mod.enable(db, platform_email="a@d.sa", staff_email="r@t.sa",
                    password="Daif-Demo-2026", hotel="فندق طيبة")
    info = demo_mod.banner(db)
    assert info["platform_email"] == "a@d.sa"
    assert info["staff_email"] == "r@t.sa"
    assert info["password"] == "Daif-Demo-2026"


def test_banner_switches_off(db):
    demo_mod.enable(db, platform_email="a@d.sa", staff_email="r@t.sa",
                    password="pw", hotel="h")
    assert demo_mod.disable(db) is True
    assert demo_mod.banner(db) is None
    assert demo_mod.disable(db) is False      # الإطفاء مرتين ليس خطأ


def test_corrupt_banner_does_not_break_the_login_page(db):
    """الشريط زينة: صفحة الدخول تُعرض حتى لو فسد محتواه."""
    db.add(PlatformSecret(name=demo_mod.ROW, value="{ ليس JSON"))
    db.flush()
    assert demo_mod.banner(db) is None


# --- الفحص المسبق لم يعد يمنع الإقلاع بسبب الأسرار ---------------------------

def test_production_boots_without_secrets_now(monkeypatch):
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    monkeypatch.setenv("DAIF_DATABASE_URL", "postgresql+psycopg://u@h/db")
    fatal = [p.key for p in preflight.check() if p.fatal]
    assert fatal == [], f"ما زال يمنع الإقلاع بسبب: {fatal}"


def test_but_it_still_warns_about_them(monkeypatch):
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    monkeypatch.setenv("DAIF_DATABASE_URL", "postgresql+psycopg://u@h/db")
    warned = [p.key for p in preflight.check()]
    assert "DAIF_SECRET_KEY" in warned and "DAIF_DASHBOARD_SECRET" in warned


def test_sqlite_in_production_is_still_fatal(monkeypatch, tmp_path):
    """القرص المؤقت ما زال يفقد البيانات — هذا لم يتغيّر."""
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    monkeypatch.setenv("DAIF_DATABASE_URL", f"sqlite:///{tmp_path}/x.db")
    assert "DATABASE_URL" in [p.key for p in preflight.check() if p.fatal]
