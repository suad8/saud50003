"""مفاتيح الواجهة البرمجية.

شكل المفتاح: `daif_<بادئة>_<سرّ>` — البادئة تُخزَّن نصًا للتمييز، والسرّ
يُخزَّن مجزَّأً. المفتاح كاملًا يُعرض مرة واحدة عند الإنشاء ولا يُسترجَع
أبدًا؛ من فقده يُنشئ غيره ويلغي القديم.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import now_riyadh
from .models import ApiKey, Tenant

PREFIX_LENGTH = 8
_KEY_RE = re.compile(r"^daif_([a-zA-Z0-9]{%d})_([A-Za-z0-9_\-]{20,})$" % PREFIX_LENGTH)


def _hash(secret: str) -> str:
    """تجزئة سريعة: المفتاح عشوائي ٣٢ بايت، فلا يحتاج إبطاءً ضد التخمين."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedKey:
    """المفتاح كما يُعرض مرة واحدة عند الإنشاء."""

    record: ApiKey
    token: str


def issue(session: Session, tenant_id: int, *, name: str, created_by: str = "") -> IssuedKey:
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    secret = secrets.token_urlsafe(32)
    record = ApiKey(
        tenant_id=tenant_id,
        name=name.strip() or "مفتاح بلا اسم",
        prefix=prefix,
        key_hash=_hash(secret),
        created_by=created_by,
    )
    session.add(record)
    session.flush()
    return IssuedKey(record=record, token=f"daif_{prefix}_{secret}")


def authenticate(session: Session, token: str | None) -> Tenant | None:
    """يعيد الفندق صاحب المفتاح، أو None.

    المقارنة على البايتات وبزمن ثابت: أي مقارنة نصية عادية تسرّب طول
    التطابق، وأي نص غير ASCII يُسقط المقارنة باستثناء.
    """
    if not token:
        return None
    match = _KEY_RE.match(token.strip())
    if not match:
        return None
    prefix, secret = match.groups()

    record = session.scalar(
        select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.active.is_(True))
    )
    if record is None:
        return None
    if not hmac.compare_digest(_hash(secret).encode("ascii"), record.key_hash.encode("ascii")):
        return None

    record.last_used_at = now_riyadh()
    tenant = session.get(Tenant, record.tenant_id)
    return tenant if tenant is not None and tenant.active else None


def bearer_token(header: str | None) -> str | None:
    """يستخرج المفتاح من ترويسة Authorization."""
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def list_keys(session: Session, tenant_id: int) -> list[ApiKey]:
    return list(
        session.scalars(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
        )
    )


def revoke(session: Session, tenant_id: int, key_id: int) -> bool:
    """يلغي مفتاحًا. الإلغاء لا الحذف — يبقى أثره في السجل."""
    record = session.get(ApiKey, key_id)
    if record is None or record.tenant_id != tenant_id:
        return False
    record.active = False
    return True
