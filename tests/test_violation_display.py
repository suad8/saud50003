"""ما يظهر تحت ردّ المساعد في شاشة المحادثات — وما لا يظهر.

هذي الشاشة يفتحها موظف الاستقبال ليقرأ ويردّ. وكانت تحمل تحت كل جملة خمس
شارات — النطاق واللغة ونسبة الثقة والوضع وملاحظة الحواجز — فتقرأ كأداة
تشخيص لا كمحادثة. و«100%» لا تعني شيئًا لمن يقرأ «سيتواصل معك الاستقبال»،
و«MissingModelKey: أضف ANTHROPIC_API_KEY» تُقرأ كعطل في المنتج.

بقيت شارة واحدة: المصدر. منه يعرف الموظف من أين جاء الجواب، وبه يصحّحه في
قاعدة المعرفة إن كان ناقصًا. والتفصيل كلّه في المحاكي، وهو مكانه.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daif.db import get_session
from daif.models import Guest, Message, StaffUser, Tenant
from daif.security import hash_password
from daif.web import app as app_module


@pytest.fixture
def screen(db):
    """يبني محادثة ويعيد صفحتها كما يراها الموظف."""
    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    db.add(StaffUser(tenant_id=tenant.id, email="owner@taibah.sa", name="ريم",
                     role="owner", password_hash=hash_password("pw12345678")))
    guest = Guest(tenant_id=tenant.id, wa_id="web:1", room="402", language="ar")
    db.add(guest)
    db.flush()

    def build(**fields):
        db.add(Message(tenant_id=tenant.id, guest_id=guest.id, direction="out",
                       language="ar", **fields))
        db.flush()
        app_module.app.dependency_overrides[get_session] = lambda: db
        try:
            with TestClient(app_module.app) as client:
                client.get("/login")
                client.post("/login",
                            data={"email": "owner@taibah.sa", "password": "pw12345678",
                                  "csrf_token": client.cookies.get("daif_csrf") or ""},
                            follow_redirects=True)
                return client.get(f"/conversations?guest={guest.id}").text
        finally:
            app_module.app.dependency_overrides.clear()

    return build


def test_no_exception_text_ever_reaches_the_screen(screen):
    body = screen(
        text="سيتواصل معك الاستقبال.", degraded=True,
        violations="عطل في النموذج: MissingModelKey: أضف ANTHROPIC_API_KEY بقيمته")

    assert "MissingModelKey" not in body
    assert "ANTHROPIC_API_KEY" not in body
    assert "عطل في النموذج" not in body


def test_a_handoff_says_so_in_one_chip(screen):
    body = screen(text="سيتواصل معك الاستقبال.", degraded=True, confidence=1.0)

    assert "حُوِّل للاستقبال" in body
    assert "100%" not in body, "نسبة الثقة لا تعني شيئًا لموظف استقبال"
    assert "وضع احتياطي" not in body


def test_an_answer_shows_where_it_came_from(screen):
    """المصدر هو الشارة الوحيدة المفيدة: به يصحّح الموظف قاعدة المعرفة."""
    body = screen(text="الواي فاي: Taibah-Guest", sources="K01", confidence=0.95)

    assert "K01" in body
    assert "95%" not in body


def test_a_staff_reply_carries_no_machine_chips_at_all(screen):
    """ردّ إنسان ليس له مصدر ولا ثقة ولا نطاق — وشاراتها عليه بلا معنى."""
    body = screen(text="جاهز، بنوصّلها لك الحين.", sent_by="ريم · الاستقبال",
                  confidence=1.0)

    assert "جاهز، بنوصّلها لك الحين." in body
    assert "حُوِّل للاستقبال" not in body
