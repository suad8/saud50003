"""رمز QR للرابط الموحّد — بلا شبكة توزيع ولا خدمة خارجية.

كان هنا توقيعٌ لكل غرفة وملصقٌ لكل باب. حُذف: الرابط صار واحدًا للفندق كله،
والنزيل يثبت نفسه بجواله لا بما في الرابط. فما بقي إلا رسم الرمز.

والترميز مكتوب هنا لا مستورَدًا من شبكة توزيع: صفحة الملصق تُفتح للطباعة في
مكتب الفندق، وقد لا يكون هناك إنترنت خارجي أصلًا.
"""

from __future__ import annotations

from urllib.parse import quote


def hotel_path(tenant_slug: str) -> str:
    """مسار باب الفندق. واحد لا يتغيّر، ولا يحمل غرفة ولا توقيعًا."""
    return f"/h/{quote(tenant_slug)}"


def hotel_url(base_url: str, tenant_slug: str) -> str:
    return base_url.rstrip("/") + hotel_path(tenant_slug)


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
        dark="#0a1a2f", light="#ffffff", xmldecl=False, svgclass=None, lineclass=None,
    )
    return buf.getvalue().decode("utf-8")
