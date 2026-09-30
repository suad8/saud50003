"""ما يقرؤه موظف الاستقبال تحت الردّ — لا ما نسجّله للتشخيص.

رسالةُ استثناء بالإنجليزية تحت كل ردّ («MissingModelKey: …») تُقرأ كعطل في
المنتج لا كملاحظة فنية، ويراها الموظف وهو يردّ على نزيل. النصّ الكامل يبقى
مخزَّنًا — في السجلّات وفي المحاكي، وهو مكان التشخيص.
"""

from __future__ import annotations

import pytest

from daif.web.violations import summarise


@pytest.mark.parametrize("raw,expected", [
    ("عطل في النموذج: MissingModelKey: مفتاح النموذج غير مضبوط. أضف "
     "ANTHROPIC_API_KEY بقيمته في إعدادات الاستضافة ثم أعد النشر.",
     "المساعد غير مفعّل"),
    ("عطل في النموذج: AuthenticationError: 401", "المساعد غير مفعّل"),
    ("عطل في النموذج: APITimeoutError: timed out", "المساعد تأخّر"),
    ("عطل في النموذج: SomethingNew: x", "تعذّر تشغيل المساعد"),
])
def test_a_technical_failure_becomes_a_short_human_line(raw, expected):
    assert summarise(raw) == expected


def test_no_english_and_no_variable_names_leak(raw="عطل في النموذج: MissingModelKey: "
                                                    "أضف ANTHROPIC_API_KEY بقيمته"):
    note = summarise(raw)
    assert "ANTHROPIC_API_KEY" not in note
    assert "MissingModelKey" not in note


def test_content_guardrails_are_kept_they_are_the_useful_ones():
    """ملاحظة عن مصدر ناقص تخصّ الجواب نفسه — وهي ما يفيد الموظف فعلًا."""
    note = summarise("استشهاد بمعرّف غير موجود: wifi | جواب داخل النطاق بلا مصدر موثّق")

    assert "استشهاد بمعرّف غير موجود" in note
    assert "بلا مصدر موثّق" in note


def test_the_same_note_twice_is_shown_once():
    twice = "عطل في النموذج: MissingModelKey: x | عطل في النموذج: MissingModelKey: y"
    assert summarise(twice) == "المساعد غير مفعّل"


def test_a_very_long_note_is_trimmed():
    assert len(summarise("م" * 500)) <= 71


def test_nothing_to_say_shows_nothing():
    assert summarise("") == "" and summarise(None) == ""


def test_the_conversation_screen_shows_the_short_form(db):
    """الاختبار الحقيقي: ما يظهر في الصفحة، لا ما ترجعه الدالة."""
    from fastapi.testclient import TestClient

    from daif.db import get_session
    from daif.models import Guest, Message, StaffUser, Tenant
    from daif.security import hash_password
    from daif.web import app as app_module

    tenant = Tenant(slug="taibah", name="فندق طيبة")
    db.add(tenant)
    db.flush()
    db.add(StaffUser(tenant_id=tenant.id, email="owner@taibah.sa", name="ريم",
                     role="owner", password_hash=hash_password("pw12345678")))
    guest = Guest(tenant_id=tenant.id, wa_id="web:1", room="402", language="ar")
    db.add(guest)
    db.flush()
    db.add(Message(
        tenant_id=tenant.id, guest_id=guest.id, direction="out",
        text="سيتواصل معك الاستقبال.", language="ar", degraded=True,
        violations="عطل في النموذج: MissingModelKey: أضف ANTHROPIC_API_KEY بقيمته"))
    db.flush()

    app_module.app.dependency_overrides[get_session] = lambda: db
    try:
        with TestClient(app_module.app) as client:
            client.get("/login")
            client.post("/login", data={"email": "owner@taibah.sa",
                                        "password": "pw12345678",
                                        "csrf_token": client.cookies.get("daif_csrf") or ""},
                        follow_redirects=True)
            body = client.get(f"/conversations?guest={guest.id}").text
    finally:
        app_module.app.dependency_overrides.clear()

    assert "المساعد غير مفعّل" in body
    # ولا أثر للنصّ الخام في الصفحة أصلًا — ولا في تلميح يظهر بمرور الفأرة.
    assert "MissingModelKey" not in body
    assert "ANTHROPIC_API_KEY" not in body
