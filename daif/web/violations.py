"""اختصار ملاحظات الحواجز لعرضها أمام موظف الاستقبال.

النصّ الكامل يبقى مخزَّنًا كما هو — وهو مادة التشخيص في المحاكي وفي
السجلّات. لكن شاشة المحادثات يقرؤها موظف وهو يردّ على نزيل، ورسالةُ
استثناء بالإنجليزية تحت كل ردّ تُقرأ كعطل في المنتج لا كملاحظة فنية.
"""

from __future__ import annotations

# بادئة الأعطال التقنية كما يكتبها المساعد.
_FAILURE = "عطل في النموذج:"

# ما يُعرض بدل النصّ الخام. المفتاح جزءٌ من الملاحظة، والقيمة ما يقرؤه الموظف.
LABELS = {
    "MissingModelKey": "المساعد غير مفعّل",
    "AuthenticationError": "المساعد غير مفعّل",
    "RateLimitError": "ضغط على المساعد",
    "APITimeoutError": "المساعد تأخّر",
    "APIConnectionError": "تعذّر الوصول للمساعد",
}

MAX = 70


def summarise(raw: str) -> str:
    """سطر قصير يفهمه موظف الاستقبال، أو فراغ إن لم يكن هناك ما يُقال."""
    text = (raw or "").strip()
    if not text:
        return ""

    shown: list[str] = []
    for note in (part.strip() for part in text.split("|")):
        if not note:
            continue
        if note.startswith(_FAILURE):
            detail = note[len(_FAILURE):].strip()
            name = detail.split(":", 1)[0].strip()
            shown.append(LABELS.get(name, "تعذّر تشغيل المساعد"))
        else:
            shown.append(note if len(note) <= MAX else note[: MAX - 1] + "…")

    seen: list[str] = []
    for note in shown:                     # نفس الملاحظة مرّتين لا تضيف شيئًا
        if note not in seen:
            seen.append(note)
    return " · ".join(seen)
