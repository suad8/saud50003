"""الفوترة والأسعار مطويّة مؤقتًا — بمفتاح لا بحذف.

العرض الحالي على أصحاب الفنادق عن الآلية والمميزات، والسعر حديثٌ لاحق.
والشيفرة كلها باقية ومختبَرة، فالعودة قلبُ مفتاح لا إعادة بناء.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daif import features
from daif.web import app as app_module


@pytest.fixture
def client(db):
    from daif.db import get_session

    app_module.app.dependency_overrides[get_session] = lambda: db
    with TestClient(app_module.app) as c:
        yield c
    app_module.app.dependency_overrides.clear()


def test_it_is_folded_away_by_default():
    assert features.billing_visible() is False


def test_the_public_page_shows_no_prices(client):
    body = client.get("/").text

    assert "ريال / شهر" not in body
    assert "الباقات والأسعار" not in body
    assert 'id="pricing"' not in body


def test_the_route_is_closed_not_just_unlinked(client):
    """إخفاء الرابط وحده يترك البابَ لمن يكتب العنوان."""
    assert client.get("/billing").status_code == 404


def test_the_public_page_still_sells_the_product(client):
    """الطيّ يخفي السعر لا القيمة — الصفحة تبقى صفحة بيع."""
    body = client.get("/").text

    assert "دخول الفنادق" in body
    assert len(body) > 4000


@pytest.mark.parametrize("value", ["1", "true", "on", "YES"])
def test_one_switch_brings_it_back(monkeypatch, client, value):
    monkeypatch.setenv("DAIF_SHOW_BILLING", value)

    assert features.billing_visible() is True
    assert "ريال / شهر" in client.get("/").text
    assert client.get("/billing").status_code != 404


def test_the_switch_can_also_force_it_off(monkeypatch):
    monkeypatch.setattr(features, "SHOW_BILLING", True)
    monkeypatch.setenv("DAIF_SHOW_BILLING", "0")

    assert features.billing_visible() is False


def test_the_template_flag_is_honest_in_both_forms(monkeypatch):
    """تمرير الدالة نفسها كان فخًّا: كائن الدالة صادق دائمًا في Jinja."""
    flag = features.SHOW_BILLING_FLAG

    assert bool(flag) is False and flag() is False

    monkeypatch.setenv("DAIF_SHOW_BILLING", "1")
    assert bool(flag) is True and flag() is True
