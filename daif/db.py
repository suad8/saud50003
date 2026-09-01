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
    """ينشئ الجداول إن لم تكن موجودة، ويضيف الأعمدة المستجدّة."""
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """ترحيل خفيف: يضيف الأعمدة التي ظهرت في النماذج ولم تُنشأ بعد.

    كافٍ لإضافة عمود بقيمة افتراضية، وهو أغلب ما يحدث في هذه المرحلة. أي
    تغيير أعقد (تبديل نوع، حذف عمود، فهرس فريد جديد) يحتاج ترحيلًا صريحًا.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
                default = column.default.arg if column.default is not None else None
                if isinstance(default, (str, int, float)) and not callable(default):
                    literal = f"'{default}'" if isinstance(default, str) else str(default)
                    ddl += f" DEFAULT {literal}"
                connection.execute(text(ddl))


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
