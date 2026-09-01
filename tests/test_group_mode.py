"""وضع المطوّف: طلب واحد يغطي عدة غرف — من سجلات الفندق لا من نص الرسالة."""

from __future__ import annotations

from dataclasses import replace

import pytest

from daif.assistant import Assistant
from daif.guardrails import enforce
from daif.models import Fact, Tenant
from daif.prompt import build_cached_system, render_operating_context
from daif.repository import get_or_create_guest, list_tickets
from daif.schema import GuestReply, ServiceRequest
from daif.service import handle_inbound


def leader_ctx(ctx, rooms=("501", "502", "503")):
    return replace(ctx, group_mode="group_leader", group_rooms=rooms)


def request_reply(room: str, rooms: list[str] | None = None) -> GuestReply:
    return GuestReply(
        intent="request", in_scope=True, language="ar",
        answer="أُبلغ التدبير الفندقي.", sources=[],
        request=ServiceRequest(type="towels", room=room, detail="مناشف إضافية",
                               rooms=rooms or []),
        confidence=0.93,
    )


# --- السياق ---------------------------------------------------------------

def test_leader_authorised_rooms_include_own_and_group(ctx):
    assert leader_ctx(ctx).authorised_rooms == ("402", "501", "502", "503")


def test_individual_guest_is_limited_to_own_room(ctx):
    assert ctx.authorised_rooms == ("402",)


def test_group_rooms_ignored_outside_group_mode(ctx):
    """قائمة غرف على نزيل فردي لا تمنحه صلاحية — الوضع هو ما يحكم."""
    sneaky = replace(ctx, group_rooms=("501", "502"))
    assert sneaky.authorised_rooms == ("402",)


# --- الحاجز ---------------------------------------------------------------

def test_leader_may_request_for_group_rooms(ctx, kb):
    out = enforce(request_reply("501", ["502"]), leader_ctx(ctx), kb)
    assert out.clean
    assert out.reply.request.all_rooms == ["501", "502"]


def test_room_outside_roster_is_dropped(ctx, kb):
    out = enforce(request_reply("501", ["999"]), leader_ctx(ctx), kb)
    assert out.reply.request.all_rooms == ["501"]
    assert "999" in out.reply.request.detail
    assert any("غير مصرّح" in v for v in out.violations)


def test_all_rooms_outside_roster_hands_off(ctx, kb):
    out = enforce(request_reply("888", ["999"]), leader_ctx(ctx), kb)
    assert out.reply.request is None
    assert out.reply.handoff.reason == "unverified_room"


def test_individual_guest_cannot_use_rooms_field(ctx, kb):
    """نزيل فردي يحاول تمرير غرف إضافية — تُسقط كلها إلا غرفته."""
    out = enforce(request_reply("402", ["403", "404"]), ctx, kb)
    assert out.reply.request.all_rooms == ["402"]


# --- البرومبت -------------------------------------------------------------

def test_group_extension_only_in_group_mode():
    plain = build_cached_system("ف", "K01 | x", group_mode="individual")
    grouped = build_cached_system("ف", "K01 | x", group_mode="group_leader")
    assert "GROUP MODE — MULTIPLE ROOMS" not in plain
    assert "GROUP MODE — MULTIPLE ROOMS" in grouped
    # نسختان فقط لكل فندق، فالتخزين المؤقت يظل فعالًا
    assert grouped.startswith(plain)


def test_authorised_rooms_travel_in_operator_context(ctx):
    block = render_operating_context(leader_ctx(ctx))
    assert "Authorised rooms:    402, 501, 502, 503" in block
    assert "Authorised rooms" not in render_operating_context(ctx)


# --- التدفق الكامل ---------------------------------------------------------

@pytest.fixture
def hotel(db):
    tenant = Tenant(slug="taibah", name="فندق طيبة", wa_phone_number_id="PN_A")
    db.add(tenant)
    db.flush()
    db.add(Fact(tenant_id=tenant.id, key="K01", text="مناشف من التدبير."))
    db.flush()
    return tenant


def test_one_message_opens_a_ticket_per_room(db, hotel, fake_reply):
    leader = get_or_create_guest(db, hotel.id, "966500000010")
    leader.room = "402"
    leader.group_mode = "group_leader"
    leader.group_rooms = "501, 502, 503"
    db.flush()

    assistant = Assistant(client=fake_reply(request_reply("501", ["502", "503"])))
    out = handle_inbound(db, hotel, wa_id="966500000010",
                         text="أبغى مناشف للغرف ٥٠١ و٥٠٢ و٥٠٣", assistant=assistant)

    assert len(out.tickets) == 3
    assert sorted(t.room for t in list_tickets(db, hotel.id)) == ["501", "502", "503"]
    assert out.ticket.room == "501"  # التوافق مع الحالة الفردية


def test_leader_cannot_reach_a_room_outside_their_group(db, hotel, fake_reply):
    leader = get_or_create_guest(db, hotel.id, "966500000010")
    leader.room = "402"
    leader.group_mode = "group_leader"
    leader.group_rooms = "501,502"
    db.flush()

    assistant = Assistant(client=fake_reply(request_reply("777")))
    out = handle_inbound(db, hotel, wa_id="966500000010",
                         text="نظفوا غرفة ٧٧٧", assistant=assistant)
    assert out.tickets == []
    assert out.handoff.reason == "unverified_room"


def test_room_list_parsing_tolerates_arabic_comma(db, hotel):
    leader = get_or_create_guest(db, hotel.id, "966500000011")
    leader.group_rooms = "501، 502 ,503,"
    db.flush()
    assert leader.room_list == ("501", "502", "503")
