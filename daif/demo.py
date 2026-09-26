"""وضع العرض: بيانات دخول معلَنة على الصفحة نفسها.

نشر تجريبي يُراد أن يجرّبه الناس بلا تنسيق مسبق. فإخفاء كلمة المرور فيه لا
يحمي شيئًا — كل من يُفترض أن يدخل يعرفها — ويكلّف كل زائر رسالةً يسأل بها.

فالوضع يُعلن نفسه بدل أن يتظاهر: شريط ظاهر فوق نموذج الدخول يحمل البيانات،
ويقول صراحةً إن المنصة تجريبية وإن ما يُكتب فيها يراه غيرك.

وهو حالة مخزَّنة لا متغيّر بيئة: يُفعَّل بأمر `demo`، ويُطفأ بضغطة من اللوحة
فور أن يصير النشر حقيقيًا — بلا إعادة نشر.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

ROW = "demo_banner"


def enable(session, *, platform_email: str, staff_email: str,
           password: str, hotel: str) -> None:
    from .models import PlatformSecret

    body = json.dumps({"platform_email": platform_email, "staff_email": staff_email,
                       "password": password, "hotel": hotel}, ensure_ascii=False)
    row = session.get(PlatformSecret, ROW)
    if row is None:
        session.add(PlatformSecret(name=ROW, value=body))
    else:
        row.value = body
    session.flush()


def disable(session) -> bool:
    """يطفئ الوضع. يعيد True إن كان مفعّلًا."""
    from .models import PlatformSecret

    row = session.get(PlatformSecret, ROW)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def banner(session) -> dict | None:
    """بيانات الشريط، أو لا شيء حين يكون الوضع مطفأً."""
    from .models import PlatformSecret

    try:
        row = session.get(PlatformSecret, ROW)
    except Exception:                      # noqa: BLE001
        # صفحة الدخول يجب أن تُعرض حتى لو تعذّرت القراءة — الشريط زينة لا شرط.
        return None
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except (ValueError, TypeError):
        logger.warning("محتوى شريط العرض غير صالح — تُجوهل.")
        return None
