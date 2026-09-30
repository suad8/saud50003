"""تطبيع أرقام الجوال — الرقم الواحد يُكتب بخمس صور.

النزيل يكتب ٠٥٠١٢٣٤٥٦٧ والفندق سجّل +966501234567، وهما رقم واحد. بلا صورة
قانونية واحدة تُخزَّن ويُبحث بها، يفشل الدخول لسبب لا يفهمه أحد: الرقم صحيح
والنظام يقول غير صحيح.

الافتراض السعودي مقصود: السوق الأول مكة والمدينة. والرقم الدولي الصريح
(يبدأ بـ + أو 00) يُحترم كما هو، فالحجّاج يأتون بأرقامهم.
"""

from __future__ import annotations

import re

DEFAULT_CC = "966"
_DIGITS = re.compile(r"\D+")

# لوحة المفاتيح العربية تكتب ٠١٢٣ لا 0123، والفارسية ۰۱۲۳. رقم صحيح يُرفض
# لأن صورته غير صورة المخزَّن عطلٌ لا يفهمه النزيل ولا الموظف.
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# أطوال معقولة لرقم دولي كامل. أقصر من ٨ ليس رقمًا، وأطول من ١٥ خارج E.164.
MIN_LEN, MAX_LEN = 8, 15


def normalize(raw: str, default_cc: str = DEFAULT_CC) -> str:
    """الصورة القانونية: أرقام فقط، برمز الدولة، بلا + ولا أصفار بادئة.

    يرجع فراغًا لما لا يصلح رقمًا — والفراغ لا يُطابِق شيئًا، فلا يفتح بابًا.
    """
    if not raw:
        return ""
    text = raw.strip().translate(_ARABIC_INDIC)
    international = text.startswith("+") or text.startswith("00")

    digits = _DIGITS.sub("", text)
    if not digits:
        return ""

    if international:
        digits = digits[2:] if text.startswith("00") else digits
    elif digits.startswith("0"):
        # صيغة محلّية: ٠٥٠… → ٩٦٦٥٠…
        digits = default_cc + digits.lstrip("0")
    elif not digits.startswith(default_cc):
        # ٥٠١٢٣٤٥٦٧ بلا صفر ولا رمز دولة — محلّي أيضًا.
        digits = default_cc + digits

    return digits if MIN_LEN <= len(digits) <= MAX_LEN else ""


def last4(raw: str) -> str:
    return _DIGITS.sub("", (raw or "").translate(_ARABIC_INDIC))[-4:]


def mask(raw: str) -> str:
    """للعرض في لوحة الموظف: آخر أربعة ظاهرة وما قبلها مخفيّ."""
    digits = _DIGITS.sub("", (raw or "").translate(_ARABIC_INDIC))
    if len(digits) <= 4:
        return digits
    return "•" * (len(digits) - 4) + digits[-4:]


def display(normalized: str) -> str:
    """الرقم كما يقرؤه موظف الاستقبال: ٠٥٣ ٢٢١ ٢٥٢٩ لا 966532212529.

    الجوال السعودي يُكتب محلّيًّا لأنه ما سيطلبه الموظف من هاتف الفندق، وما
    عداه يُكتب دوليًّا بعلامة + لأن الرقم الأجنبي بلا رمز دولته لا يُطلب.
    """
    digits = _DIGITS.sub("", normalized or "")
    if not digits:
        return ""
    if digits.startswith(DEFAULT_CC + "5") and len(digits) == len(DEFAULT_CC) + 9:
        local = "0" + digits[len(DEFAULT_CC):]
        return f"{local[:3]} {local[3:6]} {local[6:]}"
    return "+" + digits


def tel(normalized: str) -> str:
    """رابط الاتصال — بصيغة دولية دائمًا، فيعمل من أي هاتف."""
    digits = _DIGITS.sub("", normalized or "")
    return f"tel:+{digits}" if digits else ""
