"""واجهة تكامل أنظمة إدارة الفنادق ومفاتيحها."""

from __future__ import annotations

import pytest

from daif import apikeys
from daif.models import Tenant


@pytest.fixture
def hotels(db):
    a = Tenant(slug="taibah", name="فندق طيبة")
    b = Tenant(slug="anwar", name="فندق الأنوار")
    db.add_all([a, b])
    db.flush()
    return a, b


# --- المفاتيح ---------------------------------------------------------------

def test_key_is_never_stored_in_clear(db, hotels):
    """تسريب القاعدة يجب ألا يمنح أحدًا حق الكتابة على غرف النزلاء."""
    a, _ = hotels
    issued = apikeys.issue(db, a.id, name="جرس")
    secret = issued.token.rsplit("_", 1)[1]
    assert secret not in issued.record.key_hash
    assert issued.record.key_hash != secret


def test_key_authenticates_its_own_hotel(db, hotels):
    a, b = hotels
    key_a = apikeys.issue(db, a.id, name="أ").token
    key_b = apikeys.issue(db, b.id, name="ب").token
    assert apikeys.authenticate(db, key_a).slug == "taibah"
    assert apikeys.authenticate(db, key_b).slug == "anwar"


@pytest.mark.parametrize(
    "bad",
    ["", None, "غير-صالح", "daif_short_x", "daif_deadbeef_" + "x" * 30, "Bearer something"],
)
def test_invalid_keys_are_refused(db, hotels, bad):
    assert apikeys.authenticate(db, bad) is None


def test_non_ascii_key_does_not_crash(db, hotels):
    """مقارنة نصية عادية تسقط باستثناء على العربية — الرفض هو الصواب."""
    assert apikeys.authenticate(db, "daif_مفتاحاً_" + "x" * 30) is None


def test_revoked_key_stops_working(db, hotels):
    a, _ = hotels
    issued = apikeys.issue(db, a.id, name="جرس")
    assert apikeys.authenticate(db, issued.token) is not None
    apikeys.revoke(db, a.id, issued.record.id)
    assert apikeys.authenticate(db, issued.token) is None


def test_key_of_suspended_hotel_stops_working(db, hotels):
    a, _ = hotels
    token = apikeys.issue(db, a.id, name="جرس").token
    a.active = False
    db.flush()
    assert apikeys.authenticate(db, token) is None


def test_cannot_revoke_another_hotels_key(db, hotels):
    a, b = hotels
    issued = apikeys.issue(db, a.id, name="أ")
    assert apikeys.revoke(db, b.id, issued.record.id) is False
    assert apikeys.authenticate(db, issued.token) is not None


def test_use_is_timestamped(db, hotels):
    a, _ = hotels
    issued = apikeys.issue(db, a.id, name="جرس")
    assert issued.record.last_used_at is None
    apikeys.authenticate(db, issued.token)
    assert issued.record.last_used_at is not None


def test_bearer_header_parsing():
    assert apikeys.bearer_token("Bearer abc") == "abc"
    assert apikeys.bearer_token("bearer abc") == "abc"
    assert apikeys.bearer_token("Basic abc") is None
    assert apikeys.bearer_token("abc") is None
    assert apikeys.bearer_token(None) is None


# --- الأشكال ---------------------------------------------------------------

def test_phone_is_normalised():
    from daif.web.api import CheckIn

    payload = CheckIn(wa_id="+966 50-000-0001", room="402")
    assert payload.wa_id == "966500000001"


def test_letters_in_phone_are_refused():
    from pydantic import ValidationError

    from daif.web.api import CheckIn

    with pytest.raises(ValidationError):
        CheckIn(wa_id="abc123456", room="402")


def test_tenant_id_cannot_be_smuggled_in_the_body():
    """الفندق يُشتقّ من المفتاح دائمًا — أي حقل زائد يُرفض."""
    from pydantic import ValidationError

    from daif.web.api import CheckIn

    with pytest.raises(ValidationError):
        CheckIn(wa_id="966500000001", room="402", tenant_id=99)


def test_unknown_group_mode_refused():
    from pydantic import ValidationError

    from daif.web.api import CheckIn

    with pytest.raises(ValidationError):
        CheckIn(wa_id="966500000001", room="402", group_mode="admin")


def test_unknown_ticket_status_refused():
    from pydantic import ValidationError

    from daif.web.api import TicketStatus

    with pytest.raises(ValidationError):
        TicketStatus(status="deleted")
    assert TicketStatus(status="done").status == "done"
