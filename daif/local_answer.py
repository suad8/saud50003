"""الجواب الفوري من قاعدة المعرفة — بلا نموذج، وبلا انتظار، وبلا تكلفة.

أكثر أسئلة النزلاء تكرارًا عشرة أسئلة، وجوابها مكتوب حرفيًا في قاعدة
المعرفة: الواي فاي، الإفطار، وقت الخروج، أقرب باب للحرم. إرسال هذي إلى
نموذج لغوي إبطاءٌ وتكلفةٌ بلا مقابل — والنموذج نفسه لا يفعل أكثر من إعادة
النص الذي أعطيناه إيّاه.

فنحاول هنا أولًا. والمطابقة تُقاس بالكلمات لا بالمعنى، فحدودها مرسومة:

  - **حقيقة واحدة بالضبط.** حقيقتان تغطيان السؤال تعنيان أننا لا نعرف
    أيّهما أراد؛ فيمضي إلى النموذج الذي يفهم السياق.
  - **عتبة تفوق ما يليها.** كلمة مشتركة بين موضوعين لا تكفي: نشترط أن
    يسبق الأول الثاني بفارق واضح، وإلا فالأمر ملتبس.
  - **الظاهر فقط.** ما حجبه الموسم أو انتهت صلاحيته لا يُقتبس. الترشيح
    يجري قبل هذا الملف، وهو يقتبس ما وصله لا أكثر.
  - **نفي أو مقارنة تُمرَّر.** «ما عندكم واي فاي؟» و«الفرق بين…» أسئلة
    تحتاج فهمًا لا اقتباسًا.
  - **العربية وحدها.** نصّ الحقائق عربي، ونزيلٌ كتب بالتركية يستحق جوابًا
    بلغته — وتلك مهمة النموذج. المطابقة هنا لا تترجم.

وما لم يُطابَق يمضي كما كان: إلى النموذج، فإن غاب فإلى موظف. الجواب هنا
اقتباسٌ حرفي لنصّ مخزَّن — لا يمكن أن يخترع معلومة لأنه لا يولّد شيئًا.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

# --- تطبيع العربية ------------------------------------------------------------

_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ة": "ه", "ؤ": "و",
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})


def normalize(text: str) -> str:
    """صورة واحدة للكلمة مهما كُتبت: «الإفطار» و«الافطار» و«الفطار»."""
    folded = unicodedata.normalize("NFKC", text or "").translate(_FOLD)
    folded = _DIACRITICS.sub("", folded)
    return " " + _NON_WORD.sub(" ", folded).lower().strip() + " "


# --- ما يمنع الاقتباس ---------------------------------------------------------

# نفيٌ أو مقارنة أو شرط: السؤال لم يعد «أين» بل «هل» و«لماذا» و«أيّهما».
DEFER_MARKERS = (
    " ما في ", " مافي ", " ما فيه ", " مو ", " مش ", " ليش ", " لماذا ",
    " الفرق ", " افضل ", " أفضل ", " مقارنه ", " بدل ", " غير ",
    " شكوى ", " مشكله ", " خربان ", " ما يشتغل ", " مايشتغل ", " معطل ",
)

# كلمات تدلّ على طلب خدمة لا سؤال عن معلومة. الطلب يفتح تذكرة، والاقتباس
# يحرمه منها — فيظنّ النزيل أن طلبه سُجِّل وهو لم يُسجَّل.
REQUEST_MARKERS = (
    " ابغى ", " ابي ", " اريد ", " ممكن ", " ارسلوا ", " ابعثوا ",
    " احتاج ", " وديلي ", " جيبوا ", " رجاء ", " لو سمحت ",
)


@dataclass(frozen=True)
class LocalAnswer:
    """اقتباس حرفي جاهز للإرسال، ومعه مصدره."""

    text: str
    fact_key: str
    topic: str
    score: int


# --- مرادفات المواضيع ----------------------------------------------------------
#
# مرتبطة بحقل `topic` في قاعدة المعرفة. وزن الكلمة عدد كلماتها: «باب الحرم»
# أدلّ من «باب»، فيترجّح عليها.

TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "wifi": ("واي فاي", "wifi", "الشبكه", "شبكه", "انترنت", "نت", "كلمه السر",
             "باسورد", "password", "الوايفاي", "وايفاي"),
    "breakfast": ("الافطار", "افطار", "فطور", "الفطور", "breakfast", "ريوق"),
    "dinner": ("العشاء", "عشاء", "dinner"),
    "suhoor": ("السحور", "سحور", "suhoor"),
    "iftar": ("فطور رمضان", "افطار الصايم", "iftar"),
    "haram_gate": ("الحرم", "باب الحرم", "اقرب باب", "المسجد النبوي", "المسجد الحرام",
                   "باب", "ابواب", "gate", "haram", "الروضه الشريفه"),
    "checkin_checkout": ("تسجيل الخروج", "وقت الخروج", "متى اطلع", "المغادره",
                         "تسجيل الدخول", "وقت الدخول", "checkout", "checkin",
                         "check out", "check in", "الخروج", "الدخول"),
    "housekeeping": ("التنظيف", "تنظيف", "نظافه", "مناشف", "منشفه", "التدبير",
                     "housekeeping", "شراشف", "وساده", "صابون"),
    "laundry": ("غسيل", "الغسيل", "مغسله", "كوي", "laundry", "ملابس"),
    "luggage": ("الامتعه", "امتعه", "شنط", "الشنط", "حقايب", "luggage", "مستودع"),
    "airport_transfer": ("المطار", "مطار", "توصيله", "نقل", "سياره", "taxi",
                         "تاكسي", "airport"),
    "wheelchair": ("كرسي متحرك", "كرسي", "wheelchair", "عربيه"),
    "zamzam": ("زمزم", "ماء زمزم", "zamzam", "ماي"),
    "prayer_room": ("مصلى", "المصلى", "صلاه", "الصلاه", "prayer"),
    "parking": ("موقف", "المواقف", "مواقف", "باركنج", "parking", "سيارتي"),
    "late_checkout": ("تاخير الخروج", "تمديد", "خروج متاخر", "late checkout"),
    "reception": ("الاستقبال", "استقبال", "الريسبشن", "reception", "التحويله"),
    "elevators": ("المصاعد", "مصعد", "الاصنصير", "lift", "elevator"),
    "groups": ("مجموعه", "المجموعه", "قاعه", "الوفد", "المطوف"),
    "pool": ("المسبح", "مسبح", "سباحه", "pool"),
}


# تُطبَّع مرة واحدة عند التحميل: «ابغى» تصير «ابغي» بعد التطبيع، فمقارنتها
# بنصّ مطبَّع دون تطبيعها لا تُطابِق أبدًا — والقائمة كلها تصير ميتة بصمت.
_MARKERS = tuple(normalize(m) for m in DEFER_MARKERS + REQUEST_MARKERS)


def _defers(text: str) -> bool:
    return any(marker.strip().join((" ", " ")) in text for marker in _MARKERS)


def score_topics(message: str) -> dict[str, int]:
    """درجة كل موضوع في هذه الرسالة. الأطول أدلّ، فيأخذ وزنًا أكبر."""
    haystack = normalize(message)
    scores: dict[str, int] = {}
    for topic, words in TOPIC_WORDS.items():
        best = 0
        for word in words:
            needle = normalize(word).strip()
            if needle and needle in haystack:
                best = max(best, len(needle.split()) * 10 + len(needle))
        if best:
            scores[topic] = best
    return scores


def answer(message: str, facts: Iterable, *, language: str = "ar",
           margin: int = 6) -> Optional[LocalAnswer]:
    """اقتباس حرفي لهذا السؤال، أو لا شيء فيمضي إلى النموذج.

    `facts` الحقائق **الظاهرة** بعد ترشيح الموسم والصلاحية — لا كلّها.
    """
    if language and language != "ar":
        return None                       # الترجمة مهمة النموذج لا المطابقة

    text = normalize(message)
    if len(text.strip()) < 3 or _defers(text):
        return None

    scores = score_topics(message)
    if not scores:
        return None

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_topic, top_score = ranked[0]
    # التباسٌ بين موضوعين: النموذج يفهم السياق، والمطابقة لا.
    if len(ranked) > 1 and top_score - ranked[1][1] < margin:
        return None

    hits = [f for f in facts if (getattr(f, "topic", "") or "") == top_topic]
    # حقيقتان في موضوع واحد متمّمتان لا متعارضتين — باب الرجال وباب النساء
    # مثلًا. عرضهما معًا أنفع من حجبهما، ولا يمكن أن يكون خطأ. وثلاثٌ فأكثر
    # ردٌّ طويل يُقرأ على جوال، فتمضي إلى النموذج ليختصر.
    if not 1 <= len(hits) <= 2:
        return None

    parts = [(getattr(f, "text", "") or "").strip() for f in hits]
    parts = [part for part in parts if part]
    if not parts:
        return None
    # المعرّف اسمه `id` في قاعدة المعرفة و`key` في صفوف قاعدة البيانات.
    keys = [getattr(f, "id", "") or getattr(f, "key", "") for f in hits]
    return LocalAnswer(text="\n".join(parts), fact_key="، ".join(k for k in keys if k),
                       topic=top_topic, score=top_score)
