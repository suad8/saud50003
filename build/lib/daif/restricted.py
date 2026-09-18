"""كشف المواضيع الممنوعة في رسالة النزيل — فحص مسبق متعدد اللغات.

مبدأ التصميم: هذا الفحص **يشدّد فقط**. إن التقط موضوعًا ممنوعًا فالنتيجة تحويل
لموظف بشري. لا يستطيع أبدًا أن يحوّل تحويلًا إلى جواب. لذلك الإيجابية الكاذبة
تكلفتها موظف يرد بدل المساعد — مقبولة. السلبية الكاذبة تكلفتها خطأ في موضوع
حسّاس — غير مقبولة.

الحدود في العربية: الكلمات العربية تلتصق بها السوابق (ال، و، ب، ل...) فلا تصلح
\\b القياسية. الكلمات القصيرة الملتبسة (حمل، ألم) تُطابق ككلمة مستقلة مع أداة
تعريف اختيارية فقط، حتى لا تلتقط «يحمل الشنطة» على أنها سؤال عن الحمل.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# فاصل غير عربي: بداية النص أو أي محرف ليس حرفًا عربيًا
_AR_EDGE = r"(?:^|[^ء-يٰ-ۓ])"
_AR_EDGE_END = r"(?:$|[^ء-يٰ-ۓ])"


def ar_token(word: str) -> str:
    """كلمة عربية مستقلة، مع أداة تعريف أو حرف جر اختياري وبلا التصاق آخر."""
    return _AR_EDGE + r"(?:ال|و|ف|ب|ل|بال|وال|فال|لل)?" + word + _AR_EDGE_END


def ar_any(word: str) -> str:
    """كلمة عربية مميّزة يكفي ورودها في أي موضع (لا لبس فيها)."""
    return word


@dataclass(frozen=True)
class RestrictedMatch:
    """نتيجة الفحص: الفئة الممنوعة والدليل الذي التقطه."""

    category: str
    evidence: str
    note: str


# وصف كل فئة بالعربي لملاحظة الموظف
CATEGORY_NOTES = {
    "prayer_times": "سؤال عن أوقات الصلاة أو الأذان أو الإقامة",
    "worship": "سؤال شرعي أو عن كيفية أداء نسك",
    "permits": "تصاريح الروضة أو نسك أو الحج والعمرة",
    "medical": "موضوع طبي أو صحي",
    "room_access": "مفاتيح أو دخول الغرف",
    "money": "فواتير أو مدفوعات أو أسعار الإقامة",
    "government": "تأشيرات أو جوازات أو أمر حكومي",
    "safety": "أمن أو سلامة أو سرقة أو شخص مفقود",
}

# ---------------------------------------------------------------------------
# أنماط قوية: ورودها وحده كافٍ للتحويل
# ---------------------------------------------------------------------------
_STRONG: list[tuple[str, str]] = [
    # --- أوقات الصلاة ---
    ("prayer_times", ar_any("أذان")),
    ("prayer_times", ar_any("اذان")),
    ("prayer_times", ar_any("الآذان")),
    ("prayer_times", ar_any("مواقيت")),
    ("prayer_times", ar_any("إقامة الصلا")),
    ("prayer_times", ar_any("وقت الصلا")),
    ("prayer_times", ar_any("أوقات الصلا")),
    ("prayer_times", ar_any("صلاة ")),
    ("prayer_times", ar_token("الفجر")),
    ("prayer_times", ar_any("التهجد")),
    ("prayer_times", ar_any("التراويح")),
    ("prayer_times", ar_any("الشروق")),
    ("prayer_times", r"\bprayer\b|\badhan\b|\bazan\b|\biqama(?:h|t)?\b"),
    ("prayer_times", r"\bfajr\b|\bdhuhr\b|\bzuhr\b|\basr\b|\bmaghrib\b|\bisha'?a?\b"),
    ("prayer_times", r"\btahajjud\b|\btaraweeh\b|\btarawih\b|\bsalah\b|\bsalat\b"),
    ("prayer_times", r"\bsholat\b|\bsolat\b|\bsubuh\b|\bwaktu solat\b"),
    ("prayer_times", r"\bezan\b|\bnamaz\b|\bimsak\b|\bnamaz vakti\b"),
    ("prayer_times", r"نماز|اوقات نماز"),
    ("prayer_times", r"নামাজ|আজান|ফজর|সালাত"),
    ("prayer_times", r"\bprière\b|\bpriere\b|\bappel à la prière\b"),
    ("prayer_times", r"\blokacin sallah\b|\bsallar\b"),
    # --- أسئلة شرعية ونُسك ---
    ("worship", ar_any("فتوى")),
    ("worship", ar_any("حكم شرعي")),
    ("worship", ar_any("هل يجوز")),
    ("worship", ar_any("يجوز لي")),
    ("worship", ar_any("طواف")),
    ("worship", ar_any("السعي بين")),
    ("worship", ar_any("إحرام")),
    ("worship", ar_any("احرام")),
    ("worship", ar_any("تلبية")),
    ("worship", ar_any("كيف أصلي")),
    ("worship", ar_any("كيف أعتمر")),
    ("worship", ar_any("دعاء")),
    ("worship", r"\bfatwa\b|\btawaf\b|\bihram\b|\bihraam\b|\btalbiyah\b"),
    ("worship", r"\bis it permissible\b|\breligious ruling\b|\bhow (?:do i|to) (?:pray|perform)\b"),
    ("worship", r"\bdoa\b|\bniat\b|\bibadah\b|\bmanasik\b"),
    # --- التصاريح ---
    ("permits", ar_any("تصريح")),
    ("permits", ar_any("تصاريح")),
    ("permits", ar_any("نسك")),
    ("permits", r"\bnusuk\b|\bpermit\b|\btasreeh\b|\btasrih\b"),
    ("permits", r"\bizin\b(?=.*\b(?:raudah|rawdah|umrah|haji)\b)"),
    # --- طبي ---
    ("medical", ar_any("مستشفى")),
    ("medical", ar_any("المستشفى")),
    ("medical", ar_any("عياد[ةت]")),
    ("medical", ar_any("إسعاف")),
    ("medical", ar_any("اسعاف")),
    ("medical", ar_any("نزيف")),
    ("medical", ar_any("إغماء")),
    ("medical", ar_any("حساسي")),
    ("medical", ar_any("سكري")),
    ("medical", ar_any("ضغط الدم")),
    ("medical", ar_any("طبيب")),
    ("medical", ar_any("دكتور")),
    ("medical", ar_token("دواء")),
    ("medical", ar_token("أدوية")),
    ("medical", ar_token("علاج")),
    ("medical", ar_token("مريض")),
    ("medical", ar_token("مريضة")),
    ("medical", ar_token("حرار[ةت]ي?")),
    ("medical", ar_token("حمى")),
    ("medical", ar_token("ألم")),
    ("medical", ar_token("وجع")),
    ("medical", ar_token("جرح")),
    ("medical", ar_token("كسر")),
    ("medical", ar_token("حامل")),
    ("medical", ar_token("حمل")),
    (
        "medical",
        r"\bmedicine\b|\bmedication\b|\bmedical\b|\bpharmac|\bdoctor\b|\bhospital\b|\bclinic\b",
    ),
    ("medical", r"\bsick\b|\bill\b|\bpain\b|\bfever\b|\bpregnan|\binjur|\bbleed|\bwound\b"),
    ("medical", r"\bambulance\b|\bdiabet|\bblood pressure\b|\ballerg|\basthma\b|\bfaint"),
    ("medical", r"\bobat\b|\bdokter\b|\brumah sakit\b|\bklinik\b|\bhamil\b|\bsakit\b"),
    ("medical", r"\bilaç\b|\bdoktor\b|\bhastane\b|\bhamile\b|\bhasta\b"),
    ("medical", r"دوا|ڈاکٹر|ہسپتال|بیمار|درد"),
    ("medical", r"ওষুধ|ডাক্তার|হাসপাতাল|অসুস্থ"),
    ("medical", r"دارو|پزشک|بیمارستان"),
    ("medical", r"\bmédicament\b|\bmédecin\b|\bmedecin\b|\bhôpital\b|\bmalade\b|\benceinte\b"),
    # --- مفاتيح ودخول الغرف ---
    ("room_access", ar_token("مفتاح")),
    ("room_access", ar_token("مفاتيح")),
    ("room_access", ar_any("كرت الغرفة")),
    ("room_access", ar_any("بطاقة الغرفة")),
    ("room_access", ar_any("افتحوا الباب")),
    ("room_access", ar_any("افتح الباب")),
    ("room_access", ar_any("الباب ما يفتح")),
    ("room_access", ar_any("ما يفتح الباب")),
    ("room_access", ar_any("قفل الباب")),
    ("room_access", r"\bkey ?card\b|\broom key\b|\bmy key\b|\bthe key\b|\bkeys\b"),
    ("room_access", r"\bopen (?:the |my )?door\b|\block(?:ed)? out\b|\bcan'?t get in(?:to)? my room\b"),
    ("room_access", r"\bkunci\b|\banahtar\b|\bclé\b|\bclef\b|چابی|کلید|চাবি"),
    # --- فواتير ومدفوعات (لا يشمل أسعار الخدمات الموثّقة) ---
    ("money", ar_any("فاتور")),
    ("money", ar_any("الفواتير")),
    ("money", ar_any("استرداد")),
    ("money", ar_any("استرجاع المبلغ")),
    ("money", ar_any("عربون")),
    ("money", ar_any("مبلغ التأمين")),
    ("money", ar_any("بطاقة ائتمان")),
    ("money", ar_any("سعر الغرفة")),
    ("money", ar_any("سعر الليلة")),
    ("money", ar_any("سعر الليله")),
    ("money", ar_any("تكلفة الإقامة")),
    ("money", ar_any("حسابي")),
    ("money", r"\binvoice\b|\breceipt\b|\brefund\b|\bdeposit\b|\bcredit card\b|\bfolio\b"),
    ("money", r"\broom rate\b|\bper night\b|\bmy bill\b|\bthe bill\b|\boverchar|\bdouble ?charg"),
    ("money", r"\bfaktur\b|\bfatura\b|\bfacture\b|\brimborso\b"),
    # --- حكومي ---
    ("government", ar_any("تأشير")),
    ("government", ar_any("فيزا")),
    ("government", ar_any("الجوازات")),
    ("government", ar_any("جواز السفر")),
    ("government", ar_any("جوازي")),
    ("government", ar_any("تجديد الإقامة")),
    ("government", ar_any("هوية مقيم")),
    ("government", ar_any("أبشر")),
    ("government", ar_any("توكلنا")),
    ("government", ar_any("الجمارك")),
    ("government", r"\bvisa\b|\bpassport\b|\bimmigration\b|\bresidenc|\biqama\b|\bcustoms\b"),
    ("government", r"\bvize\b|\bpaspor\b|\bviza\b|ویزا|ভিসা|پاسپورٹ"),
    # --- أمن وسلامة ---
    ("safety", ar_any("حريق")),
    ("safety", ar_any("دخان")),
    ("safety", ar_any("إنذار")),
    ("safety", ar_any("انذار")),
    ("safety", ar_any("سرق")),
    ("safety", ar_any("انسرق")),
    ("safety", ar_any("مفقود")),
    ("safety", ar_any("الشرطة")),
    ("safety", ar_any("تحرش")),
    ("safety", ar_any("اعتداء")),
    ("safety", ar_any("إخلاء")),
    ("safety", ar_token("ضاع")),
    ("safety", ar_token("ضاعت")),
    ("safety", r"\bfire\b|\bsmoke\b|\balarm\b|\btheft\b|\bstolen\b|\brobbed\b|\bburglar"),
    ("safety", r"\bpolice\b|\bemergency\b|\bevacuat|\bharass|\bassault\b|\bunsafe\b"),
    ("safety", r"\bmissing (?:child|person|man|woman|boy|girl|kid)\b|\blost my (?:child|son|daughter)\b"),
    ("safety", r"\bkebakaran\b|\bpencurian\b|\byangın\b|\bhırsızlık\b|\bincendie\b|\bvol\b"),
]

# ---------------------------------------------------------------------------
# أنماط مزدوجة: كلمة ملتبسة لا تكفي وحدها، تحتاج قرينة
# «الروضة» اسم مطعم في كثير من الفنادق، و«عمرة» قد ترد في حديث عادي.
# ---------------------------------------------------------------------------
_PAIRS: list[tuple[str, str, str]] = [
    (
        "permits",
        r"روضة|الروضه|\braudah\b|\brawdah\b|\brawdha\b|\briyadul\b",
        r"تصريح|حجز|موعد|دخول|زيارة|\bpermit\b|\bbooking\b|\bappointment\b|\bentry\b|\bhow\b|كيف",
    ),
    (
        "permits",
        r"عمرة|عمره|حج\b|\bumrah\b|\bumroh\b|\bhajj\b|\bhaji\b|\bziyarah\b",
        r"تصريح|حجز|موعد|تسجيل|كيف|\bpermit\b|\bbooking\b|\bregister\b|\bappointment\b|\bhow\b",
    ),
    (
        "worship",
        r"حلال|حرام|\bhalal\b|\bharam\b(?!ain)",
        r"هل يجوز|حكم|شرعا|شرعًا|\bruling\b|\bpermissible\b|\ballowed in islam\b",
    ),
    (
        "money",
        r"دفع|سداد|\bpay\b|\bpayment\b|\bcharge",
        r"غرفة|إقامة|ليلة|حجز|\broom\b|\bstay\b|\bnight\b|\bbooking\b|\bcheck ?out\b",
    ),
]

_STRONG_COMPILED = [(cat, re.compile(pat, re.IGNORECASE)) for cat, pat in _STRONG]
_PAIRS_COMPILED = [
    (cat, re.compile(a, re.IGNORECASE), re.compile(b, re.IGNORECASE)) for cat, a, b in _PAIRS
]


def screen(text: str) -> RestrictedMatch | None:
    """يفحص رسالة النزيل. يعيد أول تطابق أو None."""
    if not text or not text.strip():
        return None
    haystack = text.strip()

    for category, pattern in _STRONG_COMPILED:
        found = pattern.search(haystack)
        if found:
            return RestrictedMatch(
                category=category,
                evidence=found.group(0).strip(),
                note=CATEGORY_NOTES[category],
            )

    for category, first, second in _PAIRS_COMPILED:
        hit_a = first.search(haystack)
        hit_b = second.search(haystack)
        if hit_a and hit_b:
            return RestrictedMatch(
                category=category,
                evidence=f"{hit_a.group(0).strip()} + {hit_b.group(0).strip()}",
                note=CATEGORY_NOTES[category],
            )

    return None
