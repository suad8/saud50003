"""اتصال قاعدة البيانات وإنشاء الجلسات."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _prepare_sqlite_path(url: str) -> None:
    """ينشئ مجلد ملف SQLite إن لم يكن موجودًا."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = Path(url[len(prefix) :])
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionFactory
    if _engine is None or url is not None:
        target = url or get_settings().database_url
        _prepare_sqlite_path(target)
        connect_args = {"check_same_thread": False} if target.startswith("sqlite") else {}
        _engine = create_engine(target, future=True, connect_args=connect_args)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db(url: str | None = None) -> Engine:
    """ينشئ الجداول إن لم تكن موجودة."""
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def session_factory() -> sessionmaker[Session]:
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """جلسة مع حفظ تلقائي وتراجع عند الخطأ."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """اعتمادية FastAPI."""
    with session_scope() as session:
        yield session
