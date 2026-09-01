"""طبقة الخدمة: من رسالة واردة إلى رد محفوظ وتذكرة مفتوحة."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from . import billing
from .assistant import Assistant, AssistantResult
from .clock import now_riyadh, parse_iso8601
from .context import GuestContext
from .models import Guest, HandoffRecord, Message, Tenant, Ticket
from .repository import conversation_history, get_or_create_guest, load_knowledge_base

logger = logging.getLogger("daif.service")


@dataclass
class Outcome:
    """ناتج معالجة رسالة واحدة، جاهز للإرسال عبر واتساب."""

    guest: Guest
    result: AssistantResult
    inbound: Message
    outbound: Message
    tickets: list[Ticket] = field(default_factory=list)
    handoff: HandoffRecord | None = None

    @property
    def ticket(self) -> Ticket | None:
        """أول تذكرة — للحالة الفردية الشائعة."""
        return self.tickets[0] if self.tickets else None

    @property
    def reply_text(self) -> str:
        return self.result.reply.answer

    @property
    def escalate(self) -> bool:
        return self.result.escalate


def build_context(tenant: Tenant, guest: Guest) -> GuestContext:
    """يبني سياق التشغيل من إعدادات الفندق وبيانات النزيل الموثّقة."""
    moment = now_riyadh()
    return GuestContext(
        hotel_name=tenant.name,
        now=moment,
        season=tenant.effective_season(moment.date()),
        # الغرفة تأتي من سجل النزيل فقط، لا من نص رسالته أبدًا.
        room=guest.room or "",
        guest_name=guest.name or "",
        hk_window_raw=tenant.hk_window,
        desk_status=tenant.desk_status,
        group_mode=guest.group_mode or "individual",
        group_rooms=guest.room_list,
    )


def handle_inbound(
    session: Session,
    tenant: Tenant,
    *,
    wa_id: str,
    text: str,
    assistant: Assistant,
    wa_message_id: str = "",
    low_confidence_input: bool = False,
) -> Outcome:
    """يعالج رسالة نزيل واردة من أولها لآخرها."""
    guest = get_or_create_guest(session, tenant.id, wa_id)
    guest.last_seen_at = now_riyadh()

    inbound = Message(
        tenant_id=tenant.id,
        guest_id=guest.id,
        direction="in",
        text=text,
        wa_message_id=wa_message_id,
    )
    session.add(inbound)
    session.flush()

    ctx = build_context(tenant, guest)
    kb = load_knowledge_base(session, tenant.id)
    history = conversation_history(
        session, tenant.id, guest.id, exclude_message_id=inbound.id
    )

    result = assistant.reply(
        ctx=ctx,
        kb=kb,
        message=text,
        history=history,
        low_confidence_input=low_confidence_input,
    )
    reply = result.reply

    if reply.language and not guest.language:
        guest.language = reply.language

    outbound = Message(
        tenant_id=tenant.id,
        guest_id=guest.id,
        direction="out",
        text=reply.answer,
        language=reply.language,
        intent=reply.intent,
        in_scope=reply.in_scope,
        sources=",".join(reply.sources),
        confidence=reply.confidence,
        violations=" | ".join(result.violations),
        restricted_category=result.restricted.category if result.restricted else "",
        degraded=result.degraded,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cache_read_tokens=result.usage.cache_read_tokens,
        cache_write_tokens=result.usage.cache_write_tokens,
        latency_ms=result.latency_ms,
    )
    session.add(outbound)
    session.flush()

    tickets: list[Ticket] = []
    if reply.request is not None:
        requested_at = parse_iso8601(reply.request.requested_time)
        # طلب المطوّف قد يغطي عدة غرف — تذكرة مستقلة لكل غرفة حتى يتابعها
        # التدبير الفندقي غرفةً غرفة بدل تذكرة واحدة غامضة.
        for room in reply.request.all_rooms:
            ticket = Ticket(
                tenant_id=tenant.id,
                guest_id=guest.id,
                message_id=outbound.id,
                type=reply.request.type,
                room=room,
                detail=reply.request.detail,
                requested_time=requested_at,
                urgency=reply.request.urgency,
                escalated=result.escalate,
            )
            session.add(ticket)
            tickets.append(ticket)

    handoff: HandoffRecord | None = None
    if reply.handoff is not None:
        handoff = HandoffRecord(
            tenant_id=tenant.id,
            guest_id=guest.id,
            message_id=outbound.id,
            reason=reply.handoff.reason,
            to=reply.handoff.to,
            note=reply.handoff.note,
            guest_text=text,
        )
        session.add(handoff)

    # قياس الاستخدام: أساس الفوترة، ويُسجَّل بعد اكتمال المعالجة لا قبلها.
    billing.record(
        session,
        tenant.id,
        inbound=1,
        outbound=1,
        tokens_in=result.usage.input_tokens + result.usage.cache_read_tokens,
        tokens_out=result.usage.output_tokens,
        tickets=len(tickets),
        handoffs=1 if handoff is not None else 0,
    )

    if result.violations:
        logger.warning(
            "حواجز فعّلت على فندق %s نزيل %s: %s",
            tenant.slug,
            wa_id,
            " | ".join(result.violations),
        )

    session.flush()
    return Outcome(
        guest=guest,
        result=result,
        inbound=inbound,
        outbound=outbound,
        tickets=tickets,
        handoff=handoff,
    )
