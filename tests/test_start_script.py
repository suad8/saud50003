"""نقطة الإقلاع: فشل الترحيلات لا يقتل الحاوية، ويُسجَّل سببه.

كان فيها عطل صامت: `cmd | tee` في sh تُرجع حالة tee — الناجحة دائمًا — فكان
فرع الفشل ميتًا وكل الآلية معطّلة بلا أن يبان. هذي الاختبارات تشغّل السكربت
فعلًا بـ alembic وuvicorn مزيّفين، فلا يمكن للعطل أن يعود بلا أن يسقط اختبار.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, *, alembic_body: str) -> tuple[subprocess.CompletedProcess, Path]:
    fake = tmp_path / "bin"
    fake.mkdir()
    _fake_bin(fake, "alembic", alembic_body)
    # uvicorn مزيّف: يطبع سطرًا ويخرج، فنعرف أن الخادم كان سيقوم.
    _fake_bin(fake, "uvicorn", 'echo "UVICORN_STARTED $*"')

    error_file = tmp_path / "startup-error"
    env = dict(os.environ)
    env["PATH"] = f"{fake}:{env['PATH']}"
    env["STARTUP_ERROR_FILE"] = str(error_file)
    env["PORT"] = "9123"
    result = subprocess.run(
        ["sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30
    )
    return result, error_file


@pytest.mark.skipif(shutil.which("sh") is None, reason="لا صدفة POSIX")
def test_successful_migration_starts_the_server(tmp_path):
    result, error_file = _run(tmp_path, alembic_body='echo "upgrade ok"; exit 0')

    assert "UVICORN_STARTED" in result.stdout
    assert "--port 9123" in result.stdout        # المنفذ المحقون لا ثابت
    assert not error_file.exists()               # لا عطل يُعرض


@pytest.mark.skipif(shutil.which("sh") is None, reason="لا صدفة POSIX")
def test_failed_migration_still_starts_the_server_and_records_why(tmp_path):
    """القاعدة كلها: الخادم يقوم ليشرح، لا يسقط بصمت."""
    result, error_file = _run(
        tmp_path,
        alembic_body='echo "psycopg.OperationalError: connection refused" >&2; exit 1',
    )

    assert "UVICORN_STARTED" in result.stdout, "الخادم لم يقم بعد فشل الترحيل"
    assert error_file.exists(), "فشل الترحيل لم يُسجَّل — فرع الفشل ميت"
    recorded = error_file.read_text(encoding="utf-8")
    assert "فشل تطبيق ترحيلات قاعدة البيانات." in recorded
    assert "connection refused" in recorded, "السبب الحقيقي ضاع من السجل"


@pytest.mark.skipif(shutil.which("sh") is None, reason="لا صدفة POSIX")
def test_a_stale_error_file_is_cleared_on_a_healthy_start(tmp_path):
    """نشرة ناجحة بعد فاشلة يجب ألا تبقى عارضة صفحة عطل قديمة."""
    fake = tmp_path / "bin"
    fake.mkdir()
    _fake_bin(fake, "alembic", "exit 0")
    _fake_bin(fake, "uvicorn", 'echo "UVICORN_STARTED"')
    error_file = tmp_path / "startup-error"
    error_file.write_text("عطل من نشرة سابقة", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{fake}:{env['PATH']}"
    env["STARTUP_ERROR_FILE"] = str(error_file)
    subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30)

    assert not error_file.exists()
