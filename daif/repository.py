"""استعلامات مقيّدة بالفندق.

كل دالة هنا تأخذ `tenant_id` صراحةً. لا يوجد استعلام يقرأ بيانات فندق دون
تحديده — هذا هو حاجز العزل بين المشتركين.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .clock import now_riyadh
from .knowledge import KnowledgeBase
from .models import (
    AuditLog,
    Fact,
    Guest,
    HandoffRecord,
    Invoice,
    Message,
    StaffUser,
    Tenant,
    Ticket,
    UsageCounter,
)


# --- الفنادق ---------------------------------------------------------------

def get_tenant(session: Session, tenant_id: int) -> Tenant | None:
    return session.get(Tenant, tenant_id)


def tenant_by_slug(session: Session, slug: str) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.slug == slug))


def tenant_by_phone_number_id(session: Session, phone_number_id: str) -> Tenant | None:
    """توجيه رسالة واتساب واردة إلى الفندق صاحب الرقم."""
    if not phone_number_id:
        return None
    return session.scalar(
        select(Tenant).where(
            Tenant.wa_phone_number_id == phone_number_id, Tenant.active.is_(True)
        )
    )


def list_tenants(session: Session, *, include_inactive: bool = True) -> list[Tenant]:
    """كل الفنادق — استعلام على مستوى المنصة لا مستوى المشترك."""
    stmt = select(Tenant)
    if not include_inactive:
        stmt = stmt.where(Tenant.active.is_(True))
    return list(session.scalars(stmt.order_by(Tenant.created_at.desc())))


def platform_stats(session: Session, period: str) -> dict:
    """مؤشرات المنصة كلها: عدد المشتركين، الإيراد الشهري المتكرر، الاستهلاك."""
    from .plans import get as get_plan

    tenants = list_tenants(session)
    active = [t for t in tenants if t.active]
    mrr = sum(get_plan(t.plan).monthly for t in active if t.plan != "trial")

    usage = session.execute(
        select(
            func.coalesce(func.sum(UsageCounter.inbound), 0),
            func.coalesce(func.sum(UsageCounter.outbound), 0),
        ).where(UsageCounter.period == period)
    ).one()

    unpaid = session.scalar(
        select(func.count(Invoice.id)).where(Invoice.status == "issued")
    ) or 0

    by_plan: dict[str, int] = {}
    for tenant in active:
        by_plan[tenant.plan] = by_plan.get(tenant.plan, 0) + 1

    return {
        "tenants": len(tenants),
        "active": len(active),
        "trials": sum(1 for t in active if t.plan == "trial"),
        "mrr": mrr,
        "inbound": int(usage[0]),
        "outbound": int(usage[1]),
        "unpaid_invoices": unpaid,
        "by_plan": by_plan,
    }


def platform_admin_by_email(session: Session, email: str):
    from .models import PlatformAdmin

    return session.scalar(
        select(PlatformAdmin).where(
            func.lower(PlatformAdmin.email) == email.strip().lower(),
            PlatformAdmin.active.is_(True),
        )
    )


# --- قاعدة المعرفة ----------------------------------------------------------

def list_facts(session: Session, tenant_id: int, *, only_active: bool = False) -> list[Fact]:
    stmt = select(Fact).where(Fact.tenant_id == tenant_id)
    if only_active:
        stmt = stmt.where(Fact.active.is_(True))
    return list(session.scalars(stmt.order_by(Fact.key)))


def search_facts(
    session: Session,
    tenant_id: int,
    *,
    query: str = "",
    season: str = "",
    status: str = "",
) -> list[Fact]:
    """يبحث في قاعدة معرفة الفندق.

    بأربعين حقيقة فأكثر يصير التمرير في الجدول أبطأ من إعادة كتابة الحقيقة،
    فالمدير يتوقف عن الصيانة أصلًا.
    """
    stmt = select(Fact).where(Fact.tenant_id == tenant_id)

    needle = query.strip()
    if needle:
        pattern = f"%{needle}%"
        stmt = stmt.where(
            Fact.text.ilike(pattern) | Fact.topic.ilike(pattern) | Fact.key.ilike(pattern)
        )
    if season in ("normal", "ramadan", "hajj"):
        # المواسم مخزّنة مفصولة بفواصل — نبحث عن الاسم كاملًا بين فاصلتين
        stmt = stmt.where(
            (Fact.seasons == season)
            | Fact.seasons.like(f"{season},%")
            | Fact.seasons.like(f"%,{season}")
            | Fact.seasons.like(f"%,{season},%")
        )
    if status == "active":
        stmt = stmt.where(Fact.active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Fact.active.is_(False))
    elif status == "expiring":
        from datetime import timedelta

        horizon = now_riyadh().date() + timedelta(days=14)
        stmt = stmt.where(Fact.valid_until.is_not(None), Fact.valid_until <= horizon)
    elif status == "paid":
        stmt = stmt.where(Fact.paid.is_(True))

    return list(session.scalars(stmt.order_by(Fact.key)))


def load_knowledge_base(session: Session, tenant_id: int) -> KnowledgeBase:
    """يبني قاعدة المعرفة التي سيراها النموذج — الحقائق المفعّلة فقط."""
    facts = list_facts(session, tenant_id, only_active=True)
    return KnowledgeBase.from_records([f.to_record() for f in facts])


def expiring_facts(session: Session, tenant_id: int, *, days: int = 14) -> list[Fact]:
    """حقائق انتهت صلاحيتها أو توشك — أخطر ما في قاعدة المعرفة معلومة قديمة."""
    from datetime import timedelta

    today = now_riyadh().date()
    horizon = today + timedelta(days=days)
    return list(
        session.scalars(
            select(Fact)
            .where(
                Fact.tenant_id == tenant_id,
                Fact.active.is_(True),
                Fact.valid_until.is_not(None),
                Fact.valid_until <= horizon,
            )
            .order_by(Fact.valid_until)
        )
    )


def next_fact_key(session: Session, tenant_id: int) -> str:
    """يولّد معرّفًا جديدًا لا يصطدم بمعرّف محذوف سابقًا."""
    keys = [
        f.key
        for f in session.scalars(select(Fact).where(Fact.tenant_id == tenant_id))
    ]
    highest = 0
    for key in keys:
        digits = "".join(ch for ch in key if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"K{highest + 1:02d}"


# --- النزلاء ---------------------------------------------------------------

def get_or_create_guest(session: Session, tenant_id: int, wa_id: str) -> Guest:
    guest = session.scalar(
        select(Guest).where(Guest.tenant_id == tenant_id, Guest.wa_id == wa_id)
    )
    if guest is None:
        guest = Guest(tenant_id=tenant_id, wa_id=wa_id)
        session.add(guest)
        session.flush()
    return guest


def list_guests(session: Session, tenant_id: int, limit: int = 200) -> list[Guest]:
    return list(
        session.scalars(
            select(Guest)
            .where(Guest.tenant_id == tenant_id)
            .order_by(Guest.last_seen_at.desc().nullslast())
            .limit(limit)
        )
    )


def count_unverified_guests(session: Session, tenant_id: int) -> int:
    """نزلاء راسلوا الفندق ولم تُربط أرقامهم بغرفة — لا تُفتح لهم تذاكر."""
    return session.scalar(
        select(func.count(Guest.id)).where(
            Guest.tenant_id == tenant_id, func.trim(Guest.room) == ""
        )
    ) or 0


# --- الموظفون ---------------------------------------------------------------

def staff_by_email(session: Session, email: str) -> StaffUser | None:
    return session.scalar(
        select(StaffUser).where(
            func.lower(StaffUser.email) == email.strip().lower(),
            StaffUser.active.is_(True),
        )
    )


def list_staff(session: Session, tenant_id: int) -> list[StaffUser]:
    return list(
        session.scalars(
            select(StaffUser)
            .where(StaffUser.tenant_id == tenant_id)
            .order_by(StaffUser.created_at)
        )
    )


def count_active_owners(session: Session, tenant_id: int) -> int:
    """عدد الملّاك النشطين — يمنع تعطيل آخر مالك وقفل الفندق خارج حسابه."""
    return session.scalar(
        select(func.count(StaffUser.id)).where(
            StaffUser.tenant_id == tenant_id,
            StaffUser.role == "owner",
            StaffUser.active.is_(True),
        )
    ) or 0


def onboarding_state(session: Session, tenant: Tenant) -> dict:
    """حالة تهيئة الفندق.

    أكثر سبب تموت فيه التجارب أن الفندق يشتغل ناقص التهيئة: قاعدة معرفة
    فارغة تعني مساعدًا يحوّل كل شيء، فيظن المدير أنه لا يعمل.
    """
    active_facts = session.scalar(
        select(func.count(Fact.id)).where(
            Fact.tenant_id == tenant.id, Fact.active.is_(True)
        )
    ) or 0
    verified_guests = session.scalar(
        select(func.count(Guest.id)).where(
            Guest.tenant_id == tenant.id, func.trim(Guest.room) != ""
        )
    ) or 0
    replies_sent = session.scalar(
        select(func.count(Message.id)).where(
            Message.tenant_id == tenant.id, Message.direction == "out"
        )
    ) or 0

    steps = [
        {
            "key": "facts",
            "done": active_facts >= 10,
            "count": active_facts,
            "target": 10,
            "href": "/knowledge",
        },
        {
            "key": "whatsapp",
            "done": bool(tenant.wa_phone_number_id and tenant.wa_access_token),
            "href": "/settings",
        },
        {
            "key": "rooms",
            "done": verified_guests > 0,
            "href": "/guests",
        },
        {
            "key": "test",
            "done": replies_sent > 0,
            "href": "/simulator",
        },
    ]
    return {
        "steps": steps,
        "done": sum(1 for s in steps if s["done"]),
        "total": len(steps),
        "complete": all(s["done"] for s in steps),
    }


# --- التذاكر والتحويلات ------------------------------------------------------

def list_tickets(
    session: Session, tenant_id: int, *, status: str | None = None, limit: int = 100
) -> list[Ticket]:
    stmt = select(Ticket).where(Ticket.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Ticket.status == status)
    return list(session.scalars(stmt.order_by(Ticket.created_at.desc()).limit(limit)))


def list_handoffs(
    session: Session, tenant_id: int, *, status: str | None = None, limit: int = 100
) -> list[HandoffRecord]:
    stmt = select(HandoffRecord).where(HandoffRecord.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(HandoffRecord.status == status)
    return list(
        session.scalars(stmt.order_by(HandoffRecord.created_at.desc()).limit(limit))
    )


def list_messages(
    session: Session, tenant_id: int, *, guest_id: int | None = None, limit: int = 100
) -> list[Message]:
    stmt = select(Message).where(Message.tenant_id == tenant_id)
    if guest_id is not None:
        stmt = stmt.where(Message.guest_id == guest_id)
    return list(session.scalars(stmt.order_by(Message.created_at.desc()).limit(limit)))


def conversation_history(
    session: Session,
    tenant_id: int,
    guest_id: int,
    *,
    turns: int = 6,
    hours: int = 24,
    exclude_message_id: int | None = None,
) -> list[dict]:
    """آخر تبادلات النزيل، بصيغة رسائل النموذج. نافذة الخدمة ٢٤ ساعة.

    `exclude_message_id` يستبعد الرسالة الجارية معالجتها — فهي تُضاف كدور
    المستخدم الحالي، ولو بقيت في التاريخ لتكررت مرتين في البرومبت.
    """
    since = now_riyadh() - timedelta(hours=hours)
    conditions = [
        Message.tenant_id == tenant_id,
        Message.guest_id == guest_id,
        Message.created_at >= since,
    ]
    if exclude_message_id is not None:
        conditions.append(Message.id != exclude_message_id)
    rows = list(
        session.scalars(
            select(Message)
            .where(*conditions)
            .order_by(Message.created_at.desc())
            .limit(turns)
        )
    )
    rows.reverse()
    history: list[dict] = []
    for row in rows:
        if not row.text.strip():
            continue
        history.append(
            {"role": "user" if row.direction == "in" else "assistant", "content": row.text}
        )
    return history


# --- الفجوات المعرفية --------------------------------------------------------

def knowledge_gaps(
    session: Session, tenant_id: int, *, days: int = 30, limit: int = 20
) -> list[tuple[str, int]]:
    """أكثر الأسئلة التي لم تجد حقيقة تغطيها — مادة لتوسيع قاعدة المعرفة."""
    since = now_riyadh() - timedelta(days=days)
    rows = session.execute(
        select(HandoffRecord.guest_text, func.count(HandoffRecord.id))
        .where(
            HandoffRecord.tenant_id == tenant_id,
            HandoffRecord.reason == "no_documented_answer",
            HandoffRecord.created_at >= since,
            HandoffRecord.guest_text != "",
        )
        .group_by(HandoffRecord.guest_text)
        .order_by(func.count(HandoffRecord.id).desc())
        .limit(limit)
    ).all()
    return [(text, count) for text, count in rows]


# --- الإحصائيات --------------------------------------------------------------

def stats(session: Session, tenant_id: int, *, days: int = 7) -> dict:
    """أرقام اللوحة الرئيسية."""
    since = now_riyadh() - timedelta(days=days)
    scope = (Message.tenant_id == tenant_id, Message.created_at >= since)

    inbound = session.scalar(
        select(func.count(Message.id)).where(*scope, Message.direction == "in")
    ) or 0
    outbound = session.scalar(
        select(func.count(Message.id)).where(*scope, Message.direction == "out")
    ) or 0
    answered = session.scalar(
        select(func.count(Message.id)).where(
            *scope, Message.direction == "out", Message.in_scope.is_(True)
        )
    ) or 0
    handoffs = session.scalar(
        select(func.count(HandoffRecord.id)).where(
            HandoffRecord.tenant_id == tenant_id, HandoffRecord.created_at >= since
        )
    ) or 0
    open_tickets = session.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.tenant_id == tenant_id, Ticket.status.in_(("open", "in_progress"))
        )
    ) or 0
    open_handoffs = session.scalar(
        select(func.count(HandoffRecord.id)).where(
            HandoffRecord.tenant_id == tenant_id, HandoffRecord.status == "open"
        )
    ) or 0
    tokens = session.execute(
        select(
            func.coalesce(func.sum(Message.input_tokens), 0),
            func.coalesce(func.sum(Message.output_tokens), 0),
            func.coalesce(func.sum(Message.cache_read_tokens), 0),
            func.coalesce(func.avg(Message.latency_ms), 0),
        ).where(*scope, Message.direction == "out")
    ).one()

    languages = session.execute(
        select(Message.language, func.count(Message.id))
        .where(*scope, Message.direction == "out", Message.language != "")
        .group_by(Message.language)
        .order_by(func.count(Message.id).desc())
    ).all()

    reasons = session.execute(
        select(HandoffRecord.reason, func.count(HandoffRecord.id))
        .where(HandoffRecord.tenant_id == tenant_id, HandoffRecord.created_at >= since)
        .group_by(HandoffRecord.reason)
        .order_by(func.count(HandoffRecord.id).desc())
    ).all()

    automation = round(answered / outbound * 100) if outbound else 0
    return {
        "days": days,
        "inbound": inbound,
        "outbound": outbound,
        "answered": answered,
        "handoffs": handoffs,
        "automation_rate": automation,
        "open_tickets": open_tickets,
        "open_handoffs": open_handoffs,
        "input_tokens": int(tokens[0]),
        "output_tokens": int(tokens[1]),
        "cache_read_tokens": int(tokens[2]),
        "avg_latency_ms": int(tokens[3]),
        "languages": [(lang, count) for lang, count in languages],
        "handoff_reasons": [(reason, count) for reason, count in reasons],
    }


# --- التدقيق ----------------------------------------------------------------

def audit(
    session: Session,
    tenant_id: int,
    actor: str,
    action: str,
    *,
    entity: str = "",
    entity_id: str = "",
    detail: str = "",
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=detail,
        )
    )
