"""الأدوار والصلاحيات.

الدور كان مخزَّنًا ولا يُفحص: أي موظف استقبال كان يستطيع تعديل قاعدة المعرفة
أو قراءة رمز واتساب. هنا تُعرَّف الصلاحيات ويُفحص الدور فعليًا.

مبدأ التصميم: الصلاحية تُمنح صراحةً. ما لم يُذكر لدور، فهو ممنوع عنه.
"""

from __future__ import annotations

from typing import Final

OWNER: Final = "owner"
MANAGER: Final = "manager"
STAFF: Final = "staff"
ROLES: Final = (OWNER, MANAGER, STAFF)

# --- الصلاحيات ---
VIEW_OVERVIEW = "view.overview"
VIEW_GUESTS = "view.guests"
VIEW_TICKETS = "view.tickets"
VIEW_HANDOFFS = "view.handoffs"
VIEW_CONVERSATIONS = "view.conversations"
VIEW_KNOWLEDGE = "view.knowledge"
VIEW_GAPS = "view.gaps"
VIEW_SIMULATOR = "view.simulator"
VIEW_SETTINGS = "view.settings"
VIEW_BILLING = "view.billing"

WRITE_GUESTS = "write.guests"
WRITE_TICKETS = "write.tickets"
WRITE_HANDOFFS = "write.handoffs"
WRITE_KNOWLEDGE = "write.knowledge"
WRITE_SETTINGS = "write.settings"
WRITE_WHATSAPP = "write.whatsapp"       # يكشف رمز الوصول — للمالك وحده
WRITE_BILLING = "write.billing"
WRITE_USERS = "write.users"

# موظف الاستقبال والتدبير: يشغّل اليوم، ولا يغيّر ما يقوله المساعد.
_STAFF: Final = frozenset({
    VIEW_OVERVIEW, VIEW_GUESTS, VIEW_TICKETS, VIEW_HANDOFFS, VIEW_CONVERSATIONS,
    WRITE_GUESTS, WRITE_TICKETS, WRITE_HANDOFFS,
})

# المدير: يملك ما يقوله المساعد وكيف يتصرّف.
_MANAGER: Final = _STAFF | frozenset({
    VIEW_KNOWLEDGE, VIEW_GAPS, VIEW_SIMULATOR, VIEW_SETTINGS,
    WRITE_KNOWLEDGE, WRITE_SETTINGS,
})

# المالك: كل ما سبق، إضافةً إلى المفاتيح والمال والمستخدمين.
_OWNER: Final = _MANAGER | frozenset({
    VIEW_BILLING, WRITE_WHATSAPP, WRITE_BILLING, WRITE_USERS,
})

PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    STAFF: _STAFF,
    MANAGER: _MANAGER,
    OWNER: _OWNER,
}


def can(role: str, permission: str) -> bool:
    """هل يملك هذا الدور هذه الصلاحية؟ دور مجهول لا يملك شيئًا."""
    return permission in PERMISSIONS.get(role, frozenset())


def permissions_for(role: str) -> frozenset[str]:
    return PERMISSIONS.get(role, frozenset())
