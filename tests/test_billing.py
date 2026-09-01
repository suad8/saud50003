"""اختبارات الفوترة: الحصص، التجاوز، الضريبة، ورمز ZATCA."""

from __future__ import annotations

import base64
from datetime import datetime

import pytest

from daif import billing
from daif.clock import RIYADH
from daif.models import Tenant
from daif.plans import BASIC, CATALOG, PRO, TRIAL, format_sar, get, sar


# --- المبالغ ---------------------------------------------------------------

def test_money_is_integer_halalas_not_floats():
    """0.1 + 0.2 لا تساوي 0.3 في الأرقام العائمة — المال بأعداد صحيحة."""
    assert sar(0.35) == 35
    assert sar(999) == 99_900
    total = sum(sar(0.1) for _ in range(10))
    assert total == sar(1.0)


def test_format_shows_two_decimals():
    assert format_sar(99_900) == "999.00"
    assert format_sar(135_010) == "1,350.10"


def test_unknown_plan_falls_back_to_least_privileged():
    assert get("enterprise-unlimited").code == TRIAL.code
    assert get("").code == TRIAL.code
    assert get("BASIC").code == BASIC.code


def test_plans_grow_monotonically():
    """باقة أغلى يجب أن تعطي أكثر وتكلّف أقل للرسالة الزائدة."""
    assert PRO.monthly > BASIC.monthly
    assert PRO.included_messages > BASIC.included_messages
    assert PRO.overage < BASIC.overage
    assert BASIC.features <= PRO.features


# --- الحصص -----------------------------------------------------------------

@pytest.fixture
def hotel(db):
    tenant = Tenant(slug="taibah", name="فندق طيبة", plan="basic", rooms=80)
    db.add(tenant)
    db.flush()
    return tenant


def test_usage_accumulates_on_one_counter(db, hotel):
    billing.record(db, hotel.id, inbound=1, outbound=1, period="2026-09")
    billing.record(db, hotel.id, inbound=1, outbound=1, tickets=1, period="2026-09")
    counter = billing.counter_for(db, hotel.id, "2026-09")
    assert (counter.inbound, counter.outbound, counter.tickets) == (2, 2, 1)


def test_periods_are_separate(db, hotel):
    billing.record(db, hotel.id, outbound=5, period="2026-09")
    billing.record(db, hotel.id, outbound=3, period="2026-10")
    assert billing.counter_for(db, hotel.id, "2026-09").outbound == 5
    assert billing.counter_for(db, hotel.id, "2026-10").outbound == 3


def test_within_quota_has_no_overage(db, hotel):
    billing.record(db, hotel.id, outbound=2_500, period="2026-09")
    state = billing.quota(db, hotel, "2026-09")
    assert state.exceeded is False
    assert state.overage_amount == 0
    assert state.percent == 83


def test_overage_is_priced_not_blocked(db, hotel):
    """قرار المنتج: التجاوز يُفوتر ولا يوقف المساعد."""
    billing.record(db, hotel.id, outbound=3_500, period="2026-09")
    state = billing.quota(db, hotel, "2026-09")
    assert state.overage_messages == 500
    assert state.overage_amount == 500 * BASIC.overage
    assert format_sar(state.overage_amount) == "175.00"


def test_near_limit_warns_before_exceeding(db, hotel):
    billing.record(db, hotel.id, outbound=2_500, period="2026-09")
    assert billing.quota(db, hotel, "2026-09").near_limit is True
    billing.record(db, hotel.id, outbound=1_000, period="2026-09")
    state = billing.quota(db, hotel, "2026-09")
    assert state.near_limit is False and state.exceeded is True


def test_room_limit_is_reported(db, hotel):
    assert billing.rooms_exceeded(hotel) is False
    hotel.rooms = 140
    assert billing.rooms_exceeded(hotel) is True


def test_unlimited_plan_never_exceeds_rooms(db, hotel):
    hotel.plan = "group"
    hotel.rooms = 5_000
    assert billing.rooms_exceeded(hotel) is False


# --- الضريبة ---------------------------------------------------------------

def test_vat_is_fifteen_percent():
    assert billing.vat_of(sar(100)) == sar(15)
    assert billing.vat_of(sar(999)) == sar(149.85)


def test_vat_rounds_half_up_on_integers():
    # 3.33 ر.س × ١٥٪ = 0.4995 -> 0.50
    assert billing.vat_of(333) == 50


# --- الفواتير --------------------------------------------------------------

def test_invoice_totals_add_up(db, hotel):
    billing.record(db, hotel.id, outbound=3_500, period="2026-09")
    invoice = billing.issue_invoice(db, hotel, "2026-09")
    assert invoice.subscription_amount == BASIC.monthly
    assert invoice.overage_amount == 500 * BASIC.overage
    assert invoice.subtotal == invoice.subscription_amount + invoice.overage_amount
    assert invoice.vat_amount == billing.vat_of(invoice.subtotal)
    assert invoice.total == invoice.subtotal + invoice.vat_amount
    assert format_sar(invoice.total) == "1,350.10"


def test_one_invoice_per_period(db, hotel):
    first = billing.issue_invoice(db, hotel, "2026-09")
    second = billing.issue_invoice(db, hotel, "2026-09")
    assert first.id == second.id


def test_free_trial_invoice_is_already_paid(db, hotel):
    hotel.plan = "trial"
    invoice = billing.issue_invoice(db, hotel, "2026-09")
    assert invoice.total == 0
    assert invoice.status == "paid"


def test_invoice_number_is_stable_and_unique(db, hotel):
    assert billing.invoice_number(7, "2026-09") == "DAIF-202609-00007"
    assert billing.invoice_number(7, "2026-10") != billing.invoice_number(7, "2026-09")


def test_periods_walk_backwards_across_years():
    assert billing.previous_period("2026-01") == "2025-12"
    assert billing.previous_period("2026-09") == "2026-08"


def test_period_of_formats_month():
    assert billing.period_of(datetime(2026, 9, 1, tzinfo=RIYADH)) == "2026-09"


# --- رمز ZATCA -------------------------------------------------------------

def decode_tlv(payload: str) -> dict[int, str]:
    raw = base64.b64decode(payload)
    fields: dict[int, str] = {}
    index = 0
    while index < len(raw):
        tag, length = raw[index], raw[index + 1]
        fields[tag] = raw[index + 2 : index + 2 + length].decode("utf-8")
        index += 2 + length
    return fields


def test_zatca_qr_carries_the_five_required_tags():
    issued = datetime(2026, 9, 1, 12, 0, tzinfo=RIYADH)
    code = billing.zatca_qr("ضيف لتقنية الضيافة", "300000000000003", issued, 135_010, 17_610)
    fields = decode_tlv(code)
    assert set(fields) == {1, 2, 3, 4, 5}
    assert fields[1] == "ضيف لتقنية الضيافة"
    assert fields[2] == "300000000000003"
    assert fields[3].startswith("2026-09-01T12:00:00")
    assert fields[4] == "1350.10"
    assert fields[5] == "176.10"


def test_invoice_embeds_a_decodable_qr(db, hotel):
    billing.record(db, hotel.id, outbound=3_000, period="2026-09")
    invoice = billing.issue_invoice(db, hotel, "2026-09")
    fields = decode_tlv(invoice.zatca_qr)
    assert fields[4] == format_sar(invoice.total).replace(",", "")
    assert fields[5] == format_sar(invoice.vat_amount).replace(",", "")
