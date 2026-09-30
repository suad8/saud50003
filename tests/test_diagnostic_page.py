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


# --- المرجع في خانة الاسم بدل خانة القيمة ---


REF_NAME = "${{ Postgres.DATABASE_URL }}"


def test_a_reference_pasted_into_the_name_field_is_named(monkeypatch, no_database_env):
    """غلط صامت تمامًا: Railway تأخذ الاسم حرفيًا فلا يقرؤه أحد.

    بلا كشفه تقول الصفحة «ما لقيت عنوانًا» فقط، والناشر يعيد نفس الخطأ.
    """
    monkeypatch.setenv(REF_NAME, "postgresql://u@h/d")

    keys = [p.key for p in preflight.check(production=True)]
    assert "خانة الاسم" in keys
    detail = next(p.detail for p in preflight.check(production=True)
                  if p.key == "خانة الاسم")
    assert REF_NAME in detail


def test_the_fix_shown_is_the_misplaced_one_not_the_generic_link_guide(
    monkeypatch, no_database_env
):
    """الشرح العام «كيف تربط قاعدة البيانات» يقود لإعادة نفس الخطأ حرفيًا."""
    monkeypatch.setenv(REF_NAME, "postgresql://u@h/d")

    html = preflight.diagnostic_html(preflight.check(production=True))
    assert "المرجع في الخانة الغلط" in html
    assert "كيف تربط قاعدة البيانات" not in html


def test_the_misplaced_name_is_listed_above_the_other_notes(monkeypatch, no_database_env):
    """سبب العطل لا يُدفن بين ملاحظات واتساب ومفتاح النموذج."""
    monkeypatch.setenv(REF_NAME, "postgresql://u@h/d")
    monkeypatch.setenv("DAIF_ENV", "production")

    html = preflight.diagnostic_html(preflight.check())
    assert html.index("خانة الاسم") < html.index("WHATSAPP_APP_SECRET")


def test_ordinary_variable_names_are_not_flagged(monkeypatch, no_database_env):
    """لا إنذار كاذب على أسماء سليمة."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "x.up.railway.app")

    assert preflight.misplaced_references() == []


def test_the_page_never_prints_the_value_of_a_misplaced_variable(
    monkeypatch, no_database_env
):
    """صفحة تشخيص عامة — طباعة القيم تسرّب كلمة مرور قاعدة البيانات."""
    monkeypatch.setenv(REF_NAME, "postgresql://user:SUPERSECRET@host/db")

    html = preflight.diagnostic_html(preflight.check(production=True))
    assert "SUPERSECRET" not in html


# --- قيمة موجودة لا تصلح عنوانًا ---


def test_an_unresolved_reference_as_the_value_is_caught(monkeypatch, no_database_env):
    """المنصّة لم تحلّ المرجع فحُفظ نصًّا. بلا هذا يصل الناشر لأثر تتبّع."""
    monkeypatch.setenv("DATABASE_URL", "${{ Postgres.DATABASE_URL }}")

    problems = [p for p in preflight.check(production=True)
                if p.key == "DATABASE_URL" and p.fatal]
    assert problems, "قيمة لا تُقرأ مرّت كأنها سليمة"
    assert "مسافات" in problems[0].detail


def test_a_value_sqlalchemy_cannot_parse_is_caught(monkeypatch, no_database_env):
    monkeypatch.setenv("DATABASE_URL", "this is not a url at all")

    problems = [p for p in preflight.check(production=True)
                if p.key == "DATABASE_URL" and p.fatal]
    assert problems
    assert "postgresql://" in problems[0].detail


def test_a_real_postgres_url_passes(monkeypatch, no_database_env):
    """لا إنذار كاذب على العنوان الذي تحقنه المنصّة فعلًا."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:s3cr3t@postgres.railway.internal:5432/railway")

    assert not [p for p in preflight.check(production=True)
                if p.key == "DATABASE_URL"]


def test_the_fix_text_shows_the_reference_without_spaces(monkeypatch, no_database_env):
    """نصّ الإصلاح نفسه كان يعرضها بمسافات — وهي سبب العطل."""
    monkeypatch.setenv("DATABASE_URL", "${{ Postgres.DATABASE_URL }}")

    html = preflight.diagnostic_html(preflight.check(production=True))
    assert "${{Postgres.DATABASE_URL}}" in html
    assert "${{ Postgres.DATABASE_URL }}" not in html
