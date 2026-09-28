"""منصة فارغة تعرض مدخلها بدل أن تقف صامتة.

بعد أول نشر لا يوجد مشغّل ولا فندق ولا حساب، فزائر النطاق يجد نموذج دخول
لا يملك له بيانات. والتجهيز من سطر الأوامر يحتاج طرفية موصولة بقاعدة
البيانات نفسها — أصعب خطوة في النشر كله.

والزرع التلقائي عند الإقلاع كان الحل الأول، ثم رُفض: يفرض فندقًا وهميًا
بكلمة مرور معروفة على كل نشر حقيقي. فصار زرًّا يظهر على الفارغة وحدها.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from daif.models import Fact, PlatformAdmin, Stay, Tenant, Ticket
from daif.web import app as app_module


@pytest.fixture
def client(db):
    from daif.db import get_session

    app_module.app.dependency_overrides[get_session] = lambda: db
    with TestClient(app_module.app) as c:
        yield c
    app_module.app.dependency_overrides.clear()


def seed_via_page(client):
    """يضغط الزر كما يضغطه المتصفّح: رمز الحماية يأتي من الصفحة نفسها."""
    client.get("/login")
    token = client.cookies.get("daif_csrf") or ""
    return client.post("/setup/demo", data={"csrf_token": token},
                       follow_redirects=False)


def test_an_empty_platform_offers_the_button(client):
    body = client.get("/login").text
    assert "جهّز منصة العرض" in body
    assert "منصة فارغة" in body


def test_pressing_it_produces_a_platform_you_can_actually_log_into(client, db):
    """الزرع بلا حساب أو بلا حقائق يترك الزائر حيث كان."""
    assert seed_via_page(client).status_code == 303

    assert db.scalar(select(Tenant)) is not None
    assert db.scalar(select(PlatformAdmin)) is not None
    assert db.scalars(select(Stay)).all(), "لا إقامات — الشات لا يُجرَّب"
    assert db.scalars(select(Fact)).all(), "لا حقائق — كل سؤال يتحوّل لموظف"
    assert db.scalars(select(Ticket)).all(), "لا تذاكر — شاشة الاستقبال فارغة"


def test_the_credentials_then_appear_on_the_login_page(client):
    """الغاية كلها: زائر يجرّب بلا ما يسأل أحدًا."""
    seed_via_page(client)
    body = client.get("/login").text

    from daif import demo as demo_mod
    assert demo_mod.PASSWORD in body
    assert "جهّز منصة العرض" not in body, "الزر بقي بعد الزرع"


def test_the_button_disappears_once_a_hotel_exists(client, db):
    """نشر حقيقي أنشأ فندقه: لا يُعرض عليه زرع عرض بعدها أبدًا."""
    db.add(Tenant(slug="real", name="فندق حقيقي"))
    db.commit()

    assert "جهّز منصة العرض" not in client.get("/login").text


def test_seeding_a_platform_that_is_not_empty_is_refused(client, db):
    """الشرط هو الحماية كلها — لا يُقحَم عرضٌ على نظام يعمل."""
    db.add(Tenant(slug="real", name="فندق حقيقي"))
    db.commit()

    assert seed_via_page(client).status_code == 409
    assert db.scalars(select(Tenant)).all() == [db.scalar(select(Tenant))]


def test_pressing_it_twice_does_not_duplicate_anything(client, db):
    """ضغطتان متتاليتان أو تحديث الصفحة بعد الإرسال."""
    seed_via_page(client)
    assert seed_via_page(client).status_code == 409
    assert len(db.scalars(select(Tenant)).all()) == 1


def test_the_button_is_not_a_bare_endpoint_anyone_can_curl(client):
    """بلا رمز حماية: طلب من موقع آخر يزرع على منصة لم يفتحها صاحبها بعد."""
    assert client.post("/setup/demo").status_code == 403
