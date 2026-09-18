"""رمز الغرفة المطبوع — ما يحمله الملصق وما لا يحمله.

الملصق ورقة على جدار. من مرّ بالغرفة صوّره، ومن غادر بقي الرابط في سجل
متصفحه. لذلك يُصمَّم على أساس أنه **عام**:

  - لا اسم، ولا رقم حجز، ولا أي بيان عن ساكن الغرفة. لو صار الملصق منشورًا
    على الإنترنت غدًا لم يُفقد شيء.
  - لا يمنح دخولًا بذاته. كل ما يفعله أنه يقول «هذه الغرفة ٤٠٢ في فندق
    طيبة». الدخول تقرّره `stay.py` بعد ذلك.

والتوقيع لا يحمي سرًّا — يمنع تلفيق ملصق. بدونه يكتب أي أحد رقم غرفة في شريط
العنوان ويطرق باب إقامة غيره، فيصل طلبه إلى الاستقبال باسم ساكنها.

`v` (نسخة الملصق) تسمح بإبطال دفعة مطبوعة كاملة: يُغيَّر سرّ الفندق فتسقط كل
الأكواد القديمة دفعة واحدة وتُعاد الطباعة.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import os

# طول التوقيع في الرابط. ١٠ أحرف من base32 ≈ ٥٠ بت: تلفيق كود صالح يحتاج
# مئات تريليونات المحاولات، وكلها تمرّ على مُحدِّد المعدّل ثم على فحص الإقامة.
SIG_LEN = 10

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"   # base32 بحروف صغيرة، بلا لبس


def _b32(raw: bytes, length: int) -> str:
    bits = int.from_bytes(raw, "big")
    out = []
    for _ in range(length):
        out.append(_ALPHABET[bits & 31])
        bits >>= 5
    return "".join(out)


def _hotel_secret(tenant_slug: str, version: str) -> bytes:
    """سرّ خاص بكل فندق مشتق من سرّ المنصة.

    الاشتقاق مقصود: تسريب ملصق فندق لا يمكّن من تلفيق ملصق فندق آخر، ولا
    يُخزَّن سرّ مستقل لكل فندق.
    """
    root = os.environ.get("DAIF_SECRET_KEY", "").strip()
    if not root:
        raise RuntimeError(
            "DAIF_SECRET_KEY غير مضبوط — لا يمكن توقيع أكواد الغرف بلا سرّ."
        )
    return hashlib.pbkdf2_hmac(
        "sha256", root.encode("utf-8"),
        f"roomqr:{tenant_slug}:{version}".encode("utf-8"), 120_000,
    )


def sign(tenant_slug: str, room: str, version: str = "1") -> str:
    mac = hmac.new(_hotel_secret(tenant_slug, version),
                   f"{version}|{room}".encode("utf-8"), hashlib.sha256).digest()
    return _b32(mac, SIG_LEN)


def verify(tenant_slug: str, room: str, signature: str, version: str = "1") -> bool:
    """مقارنة ثابتة الزمن — فرق التوقيت بين توقيع قريب وبعيد يسرّب التخمين."""
    if not signature:
        return False
    return hmac.compare_digest(sign(tenant_slug, room, version), signature.strip().lower())


def sticker_path(tenant_slug: str, room: str, version: str = "1") -> str:
    """المسار الذي يفتحه المسح. الغرفة ظاهرة فيه عمدًا: الموظف يقرأ الملصق
    ويعرف غرفته بلا أداة، والسرّية ليست في الرابط أصلًا."""
    return f"/g/{quote(tenant_slug)}/{quote(room)}/{sign(tenant_slug, room, version)}"


def sticker_url(base_url: str, tenant_slug: str, room: str, version: str = "1") -> str:
    return base_url.rstrip("/") + sticker_path(tenant_slug, room, version)


def qr_svg(url: str, scale: int = 4) -> str:
    """رمز QR جاهز للطباعة.

    SVG مضمّن لا صورة: يطبع حادًّا على أي مقاس، ولا يحتاج طلبًا ثانيًا من
    الخادم — وصفحة ملصقات لتسعين غرفة كانت ستصير تسعين طلبًا.
    مستوى تصحيح الخطأ متوسط: الملصق يُلصق على جدار وقد يُخدش أو يتسخ.
    """
    import io as _io

    import segno

    buf = _io.BytesIO()
    segno.make(url, error="m", micro=False).save(
        buf, kind="svg", scale=scale, border=4,
        dark="#0f3d2e", light="#ffffff", xmldecl=False, svgclass=None, lineclass=None,
    )
    return buf.getvalue().decode("utf-8")


@dataclass(frozen=True)
class Sticker:
    """ملصق غرفة واحدة، جاهز للطباعة."""

    room: str
    url: str
    version: str = "1"

    @property
    def svg(self) -> str:
        return qr_svg(self.url)


def sheet(base_url: str, tenant_slug: str, rooms: list[str],
          version: str = "1") -> list[Sticker]:
    """دفعة ملصقات لكل غرف الفندق — تُطبع مرة عند التركيب."""
    return [Sticker(room=r, url=sticker_url(base_url, tenant_slug, r, version),
                    version=version) for r in rooms]
