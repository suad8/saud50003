"""كتالوج الباقات.

المبالغ بالهللات (١ ريال = ١٠٠ هللة) وليست عشرية عائمة. المال لا يُحسب
بأرقام عائمة: 0.1 + 0.2 لا تساوي 0.3 في الحاسوب، وفاتورة بريال ناقص تُفقد
ثقة العميل أسرع مما تُكسب.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

HALALA: Final = 100  # هللة في الريال
VAT_RATE_BP: Final = 1500  # ١٥٪ بنقاط الأساس — عدد صحيح لا كسر عائم


def sar(amount: float) -> int:
    """يحوّل مبلغًا بالريال إلى هللات."""
    return round(amount * HALALA)


def format_sar(halalas: int) -> str:
    """يعرض المبلغ بالريال بخانتين عشريتين."""
    return f"{halalas / HALALA:,.2f}"


# --- المزايا ---
GROUP_MODE = "group_mode"
ANALYTICS = "analytics"
SIMULATOR = "simulator"
MULTI_BRANCH = "multi_branch"
PUBLIC_API = "api"
KSA_HOSTING = "ksa_hosting"
PRIORITY_SUPPORT = "priority_support"

FEATURE_NAMES: Final[dict[str, str]] = {
    GROUP_MODE: "وضع المطوّف",
    ANALYTICS: "الإحصائيات والفجوات",
    SIMULATOR: "محاكي الرسائل",
    MULTI_BRANCH: "تعدد الفروع",
    PUBLIC_API: "واجهة برمجية",
    KSA_HOSTING: "استضافة داخل السعودية",
    PRIORITY_SUPPORT: "دعم ذو أولوية",
}


# ترتيب العرض: من الأعمّ نفعًا إلى الأخصّ
FEATURE_ORDER: Final[tuple[str, ...]] = (
    ANALYTICS, SIMULATOR, GROUP_MODE, MULTI_BRANCH,
    PRIORITY_SUPPORT, PUBLIC_API, KSA_HOSTING,
)


@dataclass(frozen=True)
class Plan:
    code: str
    name_ar: str
    name_en: str
    monthly: int            # هللات
    max_rooms: int          # ٠ = بلا حد
    included_messages: int  # رسائل صادرة شهريًا
    overage: int            # هللات لكل رسالة بعد الحصة
    features: frozenset[str]
    trial_days: int = 0

    @property
    def monthly_sar(self) -> str:
        return format_sar(self.monthly)

    @property
    def unlimited_rooms(self) -> bool:
        return self.max_rooms == 0

    def has(self, feature: str) -> bool:
        return feature in self.features

    @property
    def ordered_features(self) -> tuple[str, ...]:
        """المزايا بترتيب عرض ثابت.

        `frozenset` لا يضمن ترتيبًا، فكانت البطاقة تعرض المزايا مرتّبة
        اختلافًا في كل تحميل — يبدو عشوائيًا لمن يقارن الباقات.
        """
        return tuple(f for f in FEATURE_ORDER if f in self.features)


TRIAL = Plan(
    code="trial",
    name_ar="تجربة",
    name_en="Trial",
    monthly=0,
    max_rooms=60,
    included_messages=600,
    overage=0,
    features=frozenset({ANALYTICS, SIMULATOR}),
    trial_days=14,
)

BASIC = Plan(
    code="basic",
    name_ar="أساسية",
    name_en="Basic",
    monthly=sar(999),
    max_rooms=100,
    included_messages=3_000,
    overage=sar(0.35),
    features=frozenset({ANALYTICS, SIMULATOR}),
)

PRO = Plan(
    code="pro",
    name_ar="احترافية",
    name_en="Professional",
    monthly=sar(2_499),
    max_rooms=300,
    included_messages=10_000,
    overage=sar(0.30),
    features=frozenset({ANALYTICS, SIMULATOR, GROUP_MODE, MULTI_BRANCH, PRIORITY_SUPPORT}),
)

# ٢٥ ألف رسالة لا ٤٠: بـ٤٠ ألفًا كان هامش هذه الباقة ٥٢٪ بينما أختاها فوق
# ٦٠٪. الباقة الأغلى يجب ألا تكون الأضعف ربحًا.
GROUP = Plan(
    code="group",
    name_ar="مجموعة فنادق",
    name_en="Hotel group",
    monthly=sar(6_999),
    max_rooms=0,
    included_messages=25_000,
    overage=sar(0.22),
    features=frozenset(FEATURE_NAMES),
)

CATALOG: Final[dict[str, Plan]] = {p.code: p for p in (TRIAL, BASIC, PRO, GROUP)}
PAID_PLANS: Final[tuple[Plan, ...]] = (BASIC, PRO, GROUP)


def get(code: str) -> Plan:
    """الباقة بالرمز. المجهول يسقط إلى التجربة — الأقل صلاحية."""
    return CATALOG.get((code or "").strip().lower(), TRIAL)
