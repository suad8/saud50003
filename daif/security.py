"""تجزئة كلمات المرور — بلا اعتماديات خارجية."""

from __future__ import annotations

import hashlib
import hmac
import os

_ITERATIONS = 240_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


# تجزئة وهمية بنفس كلفة الحقيقية: تُستعمل حين لا يوجد المستخدم أصلًا،
# فيتساوى زمن الرد ولا يكشف أي البُرد مسجّلة.
_DUMMY_HASH = f"{_ALGO}${_ITERATIONS}$" + "00" * 16 + "$" + "00" * 32


def verify_password_constant_time(password: str, stored: str | None) -> bool:
    """يتحقق من كلمة المرور، ويستهلك نفس الزمن حتى لو لم يوجد المستخدم."""
    return verify_password(password, stored or _DUMMY_HASH) and stored is not None


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != _ALGO:
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected.hex(), digest_hex)
