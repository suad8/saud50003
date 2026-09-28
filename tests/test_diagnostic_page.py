"""حين يفشل الإقلاع: صفحة تشرح، لا خادم ميت.

«Application failed to respond» لا تقول شيئًا لمن ينشر أول مرة. هذي
الاختبارات تحرس القاعدة: أي عطل في الإعداد يصير صفحة مقروءة.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daif import config, preflight


@pytest.fixture
def blocked(monkeypatch, tmp_path):
    """خادم أُوقف تطبيقه لنقص في الإعداد."""
    from daif.web import app as app_module

    monkeypatch.setenv("DAIF_ENV", "production")
    # مسار مؤقت: التأكيد على أن العنوان sqlite، لا على مكانه. ومسار نسبي
    # هنا كان يترك ملف قاعدة بيانات في جذر المستودع بعد كل تشغيل.
    monkeypatch.setenv("DAIF_DATABASE_URL", f"sqlite:///{tmp_path}/blocked.db")
    monkeypatch.delenv("STARTUP_ERROR_FILE", raising=False)
    with TestClient(app_module.app) as client:
        yield client
    app_module._BLOCKED.clear()


def test_the_page_explains_instead_of_dying(blocked):
    r = blocked.get("/")
    assert r.status_code == 503
    assert "فحص" in r.text
    assert "DATABASE_URL" in r.text


def test_every_path_shows_it_not_a_blank_error(blocked):
    for path in ("/", "/login", "/platform/login", "/stays"):
        r = blocked.get(path)
        assert r.status_code == 503, path
        assert "الإعداد ناقص" in r.text, path


def test_health_stays_green_so_traffic_reaches_the_page(blocked):
    """لو أرجعنا خطأ على /healthz لأوقفت الاستضافة التوجيه ولما رأى أحد الصفحة."""
    r = blocked.get("/healthz")
    assert r.status_code == 200


def test_it_names_the_fix_for_a_missing_database(blocked):
    body = blocked.get("/").text
    assert "Postgres.DATABASE_URL" in body, "ما شرح كيف يربط قاعدة البيانات"
    assert "Variables" in body


def test_a_healthy_app_shows_no_diagnostic(db, monkeypatch):
    from daif import db as db_module
    from daif.web import app as app_module

    monkeypatch.delenv("DAIF_ENV", raising=False)
    app_module.app.dependency_overrides[db_module.get_session] = lambda: db
    with TestClient(app_module.app) as client:
        r = client.get("/login")
        assert r.status_code == 200
        assert "الإعداد ناقص" not in r.text
    app_module.app.dependency_overrides.clear()
    app_module._BLOCKED.clear()


def test_a_failed_migration_becomes_a_page(monkeypatch, tmp_path):
    """سقوط الترحيلات كان يقتل الحاوية قبل أن يقوم الخادم."""
    from daif.web import app as app_module

    err = tmp_path / "startup-error"
    err.write_text("فشل تطبيق ترحيلات قاعدة البيانات.\n—\nconnection refused",
                   encoding="utf-8")
    monkeypatch.setenv("STARTUP_ERROR_FILE", str(err))
    monkeypatch.delenv("DAIF_ENV", raising=False)     # قاتل حتى خارج الإنتاج

    with TestClient(app_module.app) as client:
        r = client.get("/")
        assert r.status_code == 503
        assert "connection refused" in r.text
    app_module._BLOCKED.clear()


def test_the_page_escapes_what_it_shows(monkeypatch, tmp_path):
    """نص الخطأ يأتي من الخارج — لا يُحقن في الصفحة كما هو."""
    from daif.web import app as app_module

    err = tmp_path / "startup-error"
    err.write_text("<script>alert(1)</script>", encoding="utf-8")
    monkeypatch.setenv("STARTUP_ERROR_FILE", str(err))
    monkeypatch.delenv("DAIF_ENV", raising=False)

    with TestClient(app_module.app) as client:
        body = client.get("/").text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body
    app_module._BLOCKED.clear()


# --- اكتشاف قاعدة البيانات عبر الأسماء التي تحقنها المنصات ---


@pytest.mark.parametrize("name", config.PG_URL_ENV_NAMES)
def test_any_known_env_name_is_accepted(monkeypatch, no_database_env, name):
    """اسم واحد من هذه يكفي — ولا يمنع الإقلاع لأن الناشر نسخ الاسم الآخر."""
    monkeypatch.setenv(name, "postgres://u:p@h:5432/d")

    assert config.discovered_database_url() == "postgresql+psycopg://u:p@h:5432/d"
    assert not [p for p in preflight.check(production=True) if p.key == "DATABASE_URL"]


def test_libpq_parts_are_assembled(monkeypatch, no_database_env):
    """بعض المنصات تحقن الأجزاء المنفصلة بلا عنوان كامل."""
    monkeypatch.setenv("PGHOST", "db.internal")
    monkeypatch.setenv("PGDATABASE", "daif")
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGPASSWORD", "p@ss word")
    monkeypatch.setenv("PGPORT", "5433")

    url = config.discovered_database_url()
    assert url == "postgresql+psycopg://postgres:p%40ss%20word@db.internal:5433/daif"


def test_missing_database_names_the_variables_it_looked_for(no_database_env):
    """رسالة العطل تسمّي ما بحثنا عنه، فيعرف الناشر أي اسم يضيف."""
    problems = [p for p in preflight.check(production=True) if p.key == "DATABASE_URL"]
    assert problems and problems[0].fatal
    assert "DATABASE_PRIVATE_URL" in problems[0].detail
    assert "PGHOST" in problems[0].detail


def test_the_blocking_problem_is_listed_first(monkeypatch):
    """الملاحظات لا تدفن السبب: القاتل في أعلى القائمة."""
    problems = [
        preflight.Problem("ANTHROPIC_API_KEY", "ملاحظة", fatal=False),
        preflight.Problem("DATABASE_URL", "هذا هو المانع", fatal=True),
    ]
    html = preflight.diagnostic_html(problems)
    assert html.index("DATABASE_URL") < html.index("ANTHROPIC_API_KEY")
