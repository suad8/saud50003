"""جاهزية النشر — ما ينكسر بعد الرفع لا قبله."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_match_the_models():
    """«alembic upgrade head» لا بد أن ينتج ما تصفه النماذج بالضبط.

    الإقلاع يستدعي create_all فيخفي أي جدول نسيناه في الترحيلات — وهكذا ضاع
    جدول مفاتيح الواجهة البرمجية دورة كاملة. هذا الاختبار يكشفه قبل النشر.
    """
    from daif.models import Base

    with tempfile.TemporaryDirectory() as tmp:
        mig, mod = f"sqlite:///{tmp}/mig.db", f"sqlite:///{tmp}/mod.db"
        env = dict(os.environ, DAIF_DATABASE_URL=mig, DAIF_SECRET_KEY="test-deploy")
        run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                             capture_output=True, text=True, env=env, cwd=ROOT)
        assert run.returncode == 0, run.stderr[-2000:]

        engine = create_engine(mod)
        Base.metadata.create_all(engine)
        from_mig, from_mod = inspect(create_engine(mig)), inspect(engine)

        tables_mig = set(from_mig.get_table_names()) - {"alembic_version"}
        tables_mod = set(from_mod.get_table_names())
        assert tables_mod - tables_mig == set(), \
            f"جداول ناقصة من الترحيلات: {sorted(tables_mod - tables_mig)}"

        for table in sorted(tables_mod & tables_mig):
            cols_mig = {c["name"] for c in from_mig.get_columns(table)}
            cols_mod = {c["name"] for c in from_mod.get_columns(table)}
            assert cols_mod - cols_mig == set(), \
                f"{table}: أعمدة ناقصة من الترحيلات -> {sorted(cols_mod - cols_mig)}"


def test_every_runtime_import_is_a_declared_dependency():
    """صورة نُشرت بلا segno فسقطت صفحة الملصقات وحدها.

    القوائم المنفصلة تنحرف بصمت، فنقيس ما يستورده الكود فعلًا لا ما نتذكّره.
    """
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    names = {d.split(">")[0].split("[")[0].split("=")[0].strip().lower()
             for d in declared["project"]["dependencies"]}
    # اسم الحزمة يختلف عن اسم الوحدة أحيانًا
    names |= {"yaml", "multipart", "dateutil", "jose"}

    third_party = set()
    for path in (ROOT / "daif").rglob("*.py"):
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                mod = line.split()[1].split(".")[0]
                if mod and not line.startswith("from .") and mod != "daif":
                    third_party.add(mod.lower())

    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    missing = {m for m in third_party - stdlib
               if m not in names and m.replace("_", "-") not in names}
    assert not missing, f"مستورَدة ولم تُعلَن في pyproject: {sorted(missing)}"


def test_dockerfile_builds_from_pyproject_not_a_second_list():
    """قائمة اعتماديات ثانية في الصورة = انحراف مؤجَّل."""
    docker = (ROOT / "Dockerfile").read_text("utf-8")
    assert "pip install --no-cache-dir ." in docker
    assert "anthropic>=" not in docker, "الصورة تعيد سرد الاعتماديات بدل قراءتها"


def test_start_script_runs_migrations_before_the_server():
    start = (ROOT / "scripts" / "start.sh").read_text("utf-8")
    assert start.index("alembic upgrade head") < start.index("uvicorn")


def test_health_endpoint_answers_without_a_model_key(monkeypatch):
    """فحص الصحة يجب ألا يعتمد على مفتاح النموذج، وإلا فشل النشر كله."""
    from fastapi.testclient import TestClient
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from daif.web.app import app

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


# --- فحص ما قبل الإقلاع -----------------------------------------------------

def test_dev_run_is_not_blocked(monkeypatch):
    """التطوير المحلي يمرّ بتنبيه لا بمنع."""
    from daif import preflight

    for key in ("DAIF_ENV", "DAIF_SECRET_KEY", "DAIF_DASHBOARD_SECRET",
                "ANTHROPIC_API_KEY", "DAIF_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    problems = preflight.check()
    assert problems and not any(p.fatal for p in problems)


def test_production_without_secrets_refuses_to_boot(monkeypatch):
    """خادم لا يقوم يُرى في دقيقة؛ خادم بنصف إعداد يُكتشف بعد أسبوع."""
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    for key in ("DAIF_SECRET_KEY", "DAIF_DASHBOARD_SECRET",
                "DAIF_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    fatal = [p.key for p in preflight.check() if p.fatal]
    assert "DAIF_SECRET_KEY" in fatal
    assert "DAIF_DASHBOARD_SECRET" in fatal


def test_production_on_sqlite_is_fatal(monkeypatch):
    """قرص منصات النشر مؤقت — SQLite يعني فقدان كل شيء مع كل نشر."""
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    monkeypatch.setenv("DAIF_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DAIF_DASHBOARD_SECRET", "y" * 40)
    monkeypatch.setenv("DAIF_DATABASE_URL", "sqlite:///./daif.db")
    assert "DATABASE_URL" in [p.key for p in preflight.check() if p.fatal]


def test_missing_model_key_never_blocks_boot(monkeypatch):
    """بلا مفتاح النموذج يحوّل المساعد للموظف — سلوك مقصود لا عطل."""
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    monkeypatch.setenv("DAIF_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DAIF_DASHBOARD_SECRET", "y" * 40)
    monkeypatch.setenv("DAIF_DATABASE_URL", "postgresql+psycopg://u@h/db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    problems = preflight.check()
    assert not [p for p in problems if p.fatal]
    assert "ANTHROPIC_API_KEY" in [p.key for p in problems]


def test_a_complete_production_config_passes(monkeypatch):
    from daif import preflight

    monkeypatch.setenv("DAIF_ENV", "production")
    monkeypatch.setenv("DAIF_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DAIF_DASHBOARD_SECRET", "y" * 40)
    monkeypatch.setenv("DAIF_DATABASE_URL", "postgresql+psycopg://u@h/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "z" * 32)
    assert preflight.check() == []
