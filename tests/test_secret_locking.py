"""سرّ لا يُحفظ لا يُسقط طلبًا.

عُثر على هذا لأن اختبارًا كان ينجح ضمن المجموعة ويفشل منفردًا: ضمن المجموعة
يكون السرّ في ذاكرة العملية فلا كتابة، ومنفردًا يُكتب — فيصطدم اتصالٌ ثانٍ
بمعاملة الطلب المفتوحة على SQLite وتقع «database is locked». وهو وارد في
الإنتاج أيضًا على القاعدة الافتراضية: أول تسجيل دخول بعد نشر نظيف.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from daif import secrets_store


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for env in secrets_store.MANAGED.values():
        monkeypatch.delenv(env, raising=False)
    secrets_store.reset_cache()
    yield
    secrets_store.reset_cache()


def test_a_locked_database_yields_a_usable_secret_not_an_error(monkeypatch, db):
    """إفشال الدخول لأننا لم نستطع *حفظ* السرّ أسوأ من استعماله بلا حفظ."""
    def _locked(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(secrets_store, "session_scope", _locked, raising=False)
    import daif.db as db_module
    monkeypatch.setattr(db_module, "session_scope", _locked)

    value = secrets_store.get("dashboard")
    assert value and len(value) >= 40


def test_the_same_secret_is_returned_within_one_run(db):
    """قيمتان مختلفتان في تشغيل واحد تُسقط جلسات الموظفين عشوائيًا."""
    assert secrets_store.get("dashboard") == secrets_store.get("dashboard")


def test_warming_persists_every_managed_secret(db):
    """بعد التسخين لا تبقى كتابة تقع أثناء طلب."""
    from sqlalchemy import select

    from daif.models import PlatformSecret

    secrets_store.warm()
    stored = {row.name for row in db.scalars(select(PlatformSecret))}
    assert set(secrets_store.MANAGED) <= stored


def test_an_env_secret_is_never_written_to_the_database(monkeypatch, db):
    """ما يُضبط في البيئة يبقى خارج قاعدة البيانات — هذا سبب ضبطه."""
    from sqlalchemy import select

    from daif.models import PlatformSecret

    monkeypatch.setenv("DAIF_DASHBOARD_SECRET", "z" * 50)
    secrets_store.reset_cache()

    assert secrets_store.get("dashboard") == "z" * 50
    assert db.get(PlatformSecret, "dashboard") is None
    assert db.scalar(select(PlatformSecret).where(
        PlatformSecret.value == "z" * 50)) is None
