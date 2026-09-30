"""مفتاح النموذج الغائب: عطل إعداد يُعلَن، لا عطل نموذج يُكتَم.

بلا المفتاح يعمل كل شيء ويبدو سليمًا — إلا أن المساعد لا يجيب عن شيء
ويحوّل كل سؤال لموظف. عطلٌ صامت يُكتشف من شكوى نزيل بعد أسبوع.

ووقع فعلًا: المتغيّر كان موجودًا في لوحة الاستضافة بقيمة فارغة، فارتدّت
رسالة المكتبة الخام «Could not resolve authentication method» في وجه
الموظف — صحيحة، ولا تقول أين المفتاح ولا مَن يضبطه.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daif import assistant as assistant_mod
from daif.assistant import Assistant, MissingModelKey, model_key_set


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_a_name_with_an_empty_value_does_not_count(monkeypatch):
    """أشيع صورة للعطل: المتغيّر أُنشئ ثم نُسي ملؤه."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert model_key_set() is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    assert model_key_set() is True


def test_building_a_client_without_a_key_says_what_to_do():
    with pytest.raises(MissingModelKey) as caught:
        _ = Assistant().client

    message = str(caught.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "الاستضافة" in message


def test_a_guest_message_degrades_with_a_readable_reason(ctx, kb):
    """النزيل يُحوَّل للاستقبال — وهو الصواب — واللوحة تقول لماذا.

    والسؤال طلبُ خدمة عمدًا: أسئلة المعلومات تُجاب من قاعدة المعرفة بلا
    نموذج، فلا تكشف غياب المفتاح.
    """
    result = Assistant().reply(ctx=ctx, kb=kb, message="أبغى مناشف إضافية")

    assert result.degraded
    assert "ANTHROPIC_API_KEY" in result.violations[0]
    assert "Could not resolve authentication" not in result.violations[0]


def test_an_explicit_client_still_works_without_the_env(fake_reply, ctx, kb):
    """الاختبارات وحقن العميل لا يحتاجان مفتاحًا."""
    from daif.schema import GuestReply

    client = fake_reply(GuestReply(intent="inquiry", in_scope=True, language="ar",
                                   answer="نعم.", sources=["K01"], confidence=0.9))
    assert not Assistant(client=client).reply(
        ctx=ctx, kb=kb, message="أبغى مناشف إضافية").degraded


# --- الإعلان في اللوحة ---


@pytest.fixture
def client(db):
    from daif.db import get_session
    from daif.models import StaffUser, Tenant
    from daif.security import hash_password
    from daif.web import app as app_module

    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    db.add(StaffUser(tenant_id=tenant.id, email="owner@taibah.sa", name="ريم",
                     role="owner", password_hash=hash_password("pw12345678")))
    db.flush()

    app_module.app.dependency_overrides[get_session] = lambda: db
    with TestClient(app_module.app) as c:
        c.get("/login")
        c.post("/login", data={"email": "owner@taibah.sa", "password": "pw12345678",
                               "csrf_token": c.cookies.get("daif_csrf") or ""},
               follow_redirects=True)
        yield c
    app_module.app.dependency_overrides.clear()


def test_the_notice_is_folded_away_by_default(client):
    """العرض على أصحاب الفنادق: شريط أصفر يُقرأ كعطل في المنتج."""
    from daif import features

    assert features.model_key_notice_visible() is False
    assert "المساعد يعمل جزئيًا" not in client.get("/").text


def test_one_switch_brings_the_notice_back(client, monkeypatch):
    """النقص حقيقي ويستحق الإعلان على نشرٍ يعمل — فالطيّ بمفتاح لا بحذف."""
    monkeypatch.setenv("DAIF_SHOW_MODEL_NOTICE", "1")

    for path in ("/", "/tickets", "/stays"):
        assert "المساعد يعمل جزئيًا" in client.get(path).text, path


def test_the_restored_notice_still_disappears_once_the_key_is_set(client, monkeypatch):
    monkeypatch.setenv("DAIF_SHOW_MODEL_NOTICE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    assert "المساعد يعمل جزئيًا" not in client.get("/").text


def test_the_notice_does_not_overstate_the_damage(client, monkeypatch):
    """قاعدة المعرفة تجيب بلا مفتاح، فقول «كل سؤال يتحوّل» يُفزع بلا سبب."""
    monkeypatch.setenv("DAIF_SHOW_MODEL_NOTICE", "1")

    body = client.get("/").text
    assert "كل سؤال يتحوّل" not in body
    assert "قاعدة معرفتك" in body
