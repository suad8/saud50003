"""صندوق الردّ في شاشة المحادثات — الطريق الوحيد ليردّ الموظف.

كان مسار الردّ موجودًا في الخادم ومختبَرًا، ولم يكن في الشاشة نموذج يوصل
إليه. فكانت الاختبارات تمرّ لأنها تطرق المسار مباشرة، والموظف لا يجد أين
يكتب. هذي الاختبارات تأخذ النموذج من الصفحة نفسها، كما يفعل المتصفّح.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from daif import stay as stays
from daif.clock import now_riyadh
from daif.models import HandoffRecord, StaffUser, Tenant
from daif.security import hash_password


@pytest.fixture
def setup(db):
    from fastapi.testclient import TestClient
    from daif import db as db_module
    from daif.web import app as app_module

    hotel = Tenant(slug="taibah", name="فندق طيبة")
    db.add(hotel)
    db.flush()
    db.add(StaffUser(tenant_id=hotel.id, email="owner@taibah.sa", name="ريم",
                     role="owner", password_hash=hash_password("pw12345678")))
    stays.open_stay(db, hotel.id, "402", guest_name="أحمد", phone="0501234567",
                    checkout_on=now_riyadh().date() + timedelta(days=2))
    db.flush()

    app_module.app.dependency_overrides[db_module.get_session] = lambda: db
    with TestClient(app_module.app) as guest, TestClient(app_module.app) as desk:
        guest.post("/h/taibah/enter", data={"phone": "0501234567", "language": "ar"},
                   follow_redirects=False)
        guest.post("/h/taibah/send", data={"text": "فيه صيدلية قريبة؟"})

        desk.get("/login")
        desk.post("/login", data={"email": "owner@taibah.sa", "password": "pw12345678",
                                  "csrf_token": desk.cookies.get("daif_csrf") or ""},
                  follow_redirects=True)
        yield guest, desk, db
    app_module.app.dependency_overrides.clear()


def _reply_form(page: str) -> tuple[str, str]:
    """الوجهة ورمز الحماية كما في الصفحة — لا كما نظنّهما."""
    form = re.search(r'<form method="post" action="(/conversations/\d+/reply)"'
                     r'.*?name="csrf_token" value="([^"]+)"', page, re.S)
    assert form, "لا صندوق ردّ في شاشة المحادثة"
    return form.group(1), form.group(2)


def test_the_conversation_screen_has_a_reply_box(setup):
    _guest, desk, _db = setup
    page = desk.get("/conversations").text

    assert "<textarea" in page and 'name="text"' in page
    _reply_form(page)


def test_a_reply_through_the_box_reaches_the_guest(setup):
    guest, desk, _db = setup
    action, token = _reply_form(desk.get("/conversations").text)

    sent = desk.post(action, data={"text": "الصيدلية النهدي جنب البوابة ٢١.",
                                   "csrf_token": token}, follow_redirects=False)
    assert sent.status_code == 303

    received = guest.get("/h/taibah/poll?after=0").json()["messages"]
    assert received[-1]["text"] == "الصيدلية النهدي جنب البوابة ٢١."
    assert received[-1]["by"] == "ريم"


def test_replying_resolves_the_handoff_with_a_valid_status(setup):
    """كانت تُكتب «closed» والحالة المعتمدة «resolved» — فيبقى زرّ الإغلاق."""
    _guest, desk, db = setup
    action, token = _reply_form(desk.get("/conversations").text)
    desk.post(action, data={"text": "أبشر", "csrf_token": token})

    handoff = db.scalar(select(HandoffRecord))
    assert handoff.status == "resolved"
    assert handoff.resolved_by == "owner@taibah.sa"


def test_no_inline_script_the_security_policy_would_drop(setup):
    """سياسة المحتوى تمنع السكربت المضمَّن — فيُهمَل بصمت لو وُضع هنا."""
    _guest, desk, _db = setup
    assert "<script>" not in desk.get("/conversations").text


def test_the_screen_opens_the_guest_who_just_wrote(setup):
    """كانت تفتح محادثة نزيلٍ آخر، فيردّ الموظف على غير من سأله.

    من كتب للتو يجب أن يكون أول القائمة، والمحادثة المفتوحة محادثته.
    """
    from daif.models import Guest

    _guest, desk, db = setup
    # نزيلٌ قديم بلا نشاط حديث — كان يسبق من كتب للتو.
    db.add(Guest(tenant_id=db.scalar(select(Tenant)).id, wa_id="demo:999",
                 room="999", language="ar"))
    db.flush()

    action, _token = _reply_form(desk.get("/conversations").text)
    writer = db.scalar(select(Guest).where(Guest.wa_id.like("web:%")))
    assert action == f"/conversations/{writer.id}/reply", "فُتحت محادثة نزيل آخر"
