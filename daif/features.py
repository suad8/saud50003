"""مفاتيح إظهار وإخفاء — للطيّ المؤقّت لا للحذف.

ميزة تُخفى بحذف شيفرتها تعود ببنائها من جديد. وميزة تُخفى بمفتاح تعود
بقلبه. الفرق يظهر بعد شهر، حين لا يتذكّر أحد ما حُذف ولا لماذا.

المفتاح هنا ثابت في الشيفرة لا متغيّر بيئة مطلوب: النظام يجب أن يعمل بلا
إعداد. ومتغيّر البيئة يبقى متاحًا لمن أراد قلبه بلا نشر.
"""

from __future__ import annotations

import os

# الفوترة والأسعار مطويّة مؤقتًا: العرض الحالي على أصحاب الفنادق عن الآلية
# والمميزات، والسعر حديثٌ لاحق. الشيفرة كلها باقية ومختبَرة.
SHOW_BILLING = False


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def billing_visible() -> bool:
    """هل تُعرض الفوترة والأسعار؟ يُقلَب من DAIF_SHOW_BILLING أو من الثابت."""
    return _flag("DAIF_SHOW_BILLING", SHOW_BILLING)


# تنبيه «المساعد يعمل جزئيًا» مطويّ: العرض الحالي على أصحاب الفنادق، وشريط
# أصفر على كل شاشة يقرؤه الزائر كعطل في المنتج لا كنقصٍ في إعداد نسخة عرض.
# النقص حقيقي ويستحق الإعلان على نشرٍ يعمل، فيُقلَب عندها بسطر واحد.
SHOW_MODEL_KEY_NOTICE = False


def model_key_notice_visible() -> bool:
    """هل يُعرض تنبيه غياب مفتاح النموذج في اللوحة؟"""
    return _flag("DAIF_SHOW_MODEL_NOTICE", SHOW_MODEL_KEY_NOTICE)


class _Flag:
    """مفتاح يُقرأ عند كل استعمال، ويصحّ بصورتيه في القوالب.

    تمرير الدالة نفسها إلى Jinja كان فخًّا: `{% if show_billing %}` يختبر
    كائن الدالة وهو صادق دائمًا، فتظهر الميزة المطويّة ولا يبين شيء. وهذا
    وقع فعلًا. فالمفتاح هنا يعرّف `__bool__` و`__call__` معًا: الصورتان
    تعطيان الجواب نفسه، فلا يبقى شكل خاطئ يُكتب.
    """

    def __init__(self, reader) -> None:
        self._reader = reader

    def __bool__(self) -> bool:
        return bool(self._reader())

    def __call__(self) -> bool:
        return bool(self._reader())

    def __repr__(self) -> str:  # pragma: no cover - للتشخيص وحده
        return f"<Flag {bool(self)}>"


SHOW_BILLING_FLAG = _Flag(billing_visible)
MODEL_KEY_NOTICE_FLAG = _Flag(model_key_notice_visible)
