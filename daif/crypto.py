"""تشفير الأسرار المخزَّنة.

رمز واتساب يعطي حاملَه حقّ إرسال رسائل باسم الفندق. تخزينه نصًا صريحًا يعني
أن أي نسخة احتياطية مسرَّبة تكفي لانتحال الفندق أمام نزلائه. يُخزَّن مشفّرًا.

مسار الترقية: القيم القديمة غير المشفّرة تُقرأ كما هي، وتُشفَّر عند أول حفظ.
البادئة `enc:v1:` هي ما يميّز المشفّر عن غيره.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("daif.crypto")

PREFIX = "enc:v1:"
_ENV_KEY = "DAIF_SECRET_KEY"


class MissingSecretKey(RuntimeError):
    """لا يوجد مفتاح تشفير — لا نخزّن سرًّا بلا حماية."""


def _fernet() -> Fernet:
    secret = os.environ.get(_ENV_KEY, "").strip()
    if not secret:
        raise MissingSecretKey(
            f"{_ENV_KEY} غير مضبوط — لا يمكن تشفير الأسرار. "
            "ولّد مفتاحًا بـ: python -c \"import secrets;print(secrets.token_urlsafe(48))\""
        )
    # اشتقاق مفتاح ٣٢ بايت من نص المفتاح، فيمكن للمشغّل استعمال أي نص طويل.
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), b"daif-secret-v1", 200_000)
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    """يشفّر نصًا. الفراغ يبقى فراغًا — لا معنى لتشفير قيمة غير موجودة."""
    if not plaintext:
        return ""
    if plaintext.startswith(PREFIX):
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{PREFIX}{token}"


def decrypt(stored: str) -> str:
    """يفكّ التشفير. القيم القديمة غير المشفّرة تُعاد كما هي."""
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        # قيمة سابقة لتفعيل التشفير. تعمل، وتُشفَّر عند أول حفظ.
        return stored
    try:
        return _fernet().decrypt(stored[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, MissingSecretKey):
        logger.error("تعذّر فكّ تشفير سرّ مخزَّن — تحقّق من DAIF_SECRET_KEY")
        return ""


def is_encrypted(stored: str) -> bool:
    return bool(stored) and stored.startswith(PREFIX)
