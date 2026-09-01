"""اختبارات الأمن: التشفير، الأدوار، CSRF، تحديد المعدّل، والترويسات."""

from __future__ import annotations

import os

import pytest

from daif import authz
from daif.ratelimit import Limit, RateLimiter


# --- تشفير الأسرار ---------------------------------------------------------

@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv("DAIF_SECRET_KEY", "مفتاح-اختبار-طويل-بما-يكفي")
    yield


def test_secret_round_trips(secret_key):
    from daif.crypto import decrypt, encrypt, is_encrypted

    token = encrypt("EAAG_super_secret")
    assert is_encrypted(token)
    assert "EAAG_super_secret" not in token
    assert decrypt(token) == "EAAG_super_secret"


def test_same_value_encrypts_differently(secret_key):
    """التشفير معشَّق بعامل عشوائي، فلا يكشف تكرار القيم."""
    from daif.crypto import encrypt

    assert encrypt("same") != encrypt("same")


def test_legacy_plaintext_still_readable(secret_key):
    """قيم ما قبل التشفير تعمل، وتُشفَّر عند أول حفظ."""
    from daif.crypto import decrypt, is_encrypted

    assert decrypt("plain_old_token") == "plain_old_token"
    assert not is_encrypted("plain_old_token")


def test_encrypting_twice_is_idempotent(secret_key):
    from daif.crypto import encrypt

    once = encrypt("value")
    assert encrypt(once) == once


def test_empty_stays_empty(secret_key):
    from daif.crypto import decrypt, encrypt

    assert encrypt("") == ""
    assert decrypt("") == ""


def test_missing_key_refuses_to_store_secret(monkeypatch):
    """بلا مفتاح لا نخزّن سرًّا — الفشل الصريح أفضل من التخزين المكشوف."""
    from daif.crypto import MissingSecretKey, encrypt

    monkeypatch.delenv("DAIF_SECRET_KEY", raising=False)
    with pytest.raises(MissingSecretKey):
        encrypt("value")


def test_wrong_key_does_not_leak_plaintext(secret_key, monkeypatch):
    from daif.crypto import decrypt, encrypt

    token = encrypt("EAAG_super_secret")
    monkeypatch.setenv("DAIF_SECRET_KEY", "مفتاح-مختلف-تمامًا")
    assert decrypt(token) == ""


# --- الأدوار ---------------------------------------------------------------

def test_staff_runs_the_day_but_does_not_change_what_the_assistant_says():
    assert authz.can(authz.STAFF, authz.WRITE_TICKETS)
    assert authz.can(authz.STAFF, authz.WRITE_GUESTS)
    assert not authz.can(authz.STAFF, authz.WRITE_KNOWLEDGE)
    assert not authz.can(authz.STAFF, authz.VIEW_SETTINGS)


def test_manager_owns_what_the_assistant_says():
    assert authz.can(authz.MANAGER, authz.WRITE_KNOWLEDGE)
    assert authz.can(authz.MANAGER, authz.WRITE_SETTINGS)
    # لكن ليس المفاتيح ولا المال
    assert not authz.can(authz.MANAGER, authz.WRITE_WHATSAPP)
    assert not authz.can(authz.MANAGER, authz.WRITE_BILLING)


def test_owner_holds_the_keys():
    assert authz.can(authz.OWNER, authz.WRITE_WHATSAPP)
    assert authz.can(authz.OWNER, authz.WRITE_BILLING)
    assert authz.can(authz.OWNER, authz.WRITE_USERS)


def test_roles_are_nested_not_overlapping():
    staff = authz.permissions_for(authz.STAFF)
    manager = authz.permissions_for(authz.MANAGER)
    owner = authz.permissions_for(authz.OWNER)
    assert staff < manager < owner


def test_unknown_role_has_nothing():
    assert authz.permissions_for("intern") == frozenset()
    assert not authz.can("", authz.VIEW_OVERVIEW)


# --- تحديد المعدّل ---------------------------------------------------------

def test_limit_blocks_after_allowance():
    rl = RateLimiter()
    limit = Limit(attempts=3, window=60)
    assert [rl.hit("k", limit, now=t) for t in (0, 1, 2, 3)] == [True, True, True, False]


def test_window_slides():
    rl = RateLimiter()
    limit = Limit(attempts=2, window=10)
    rl.hit("k", limit, now=0)
    rl.hit("k", limit, now=1)
    assert rl.hit("k", limit, now=5) is False
    assert rl.hit("k", limit, now=11) is True   # انقضت الأولى


def test_keys_are_independent():
    rl = RateLimiter()
    limit = Limit(attempts=1, window=60)
    assert rl.hit("a", limit, now=0) is True
    assert rl.hit("b", limit, now=0) is True
    assert rl.hit("a", limit, now=0) is False


def test_reset_clears_one_key_only():
    rl = RateLimiter()
    limit = Limit(attempts=1, window=60)
    rl.hit("a", limit, now=0)
    rl.hit("b", limit, now=0)
    rl.reset("a")
    assert rl.hit("a", limit, now=0) is True
    assert rl.hit("b", limit, now=0) is False


def test_remaining_reports_headroom():
    rl = RateLimiter()
    limit = Limit(attempts=3, window=60)
    rl.hit("k", limit, now=0)
    assert rl.remaining("k", limit, now=0) == 2
