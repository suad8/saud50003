"""جدولة المواسم، تنبيهات انتهاء الحقائق، وقناة التصعيد."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from daif.escalation import build_payload, notify
from daif.models import Fact, Tenant
from daif.repository import expiring_facts


# --- جدولة المواسم ---------------------------------------------------------

def scheduled_hotel(**overrides) -> Tenant:
    base = dict(
        slug="t", name="فندق", season="normal", season_auto=True,
        ramadan_start=date(2027, 2, 8), ramadan_end=date(2027, 3, 9),
        hajj_start=date(2027, 5, 10), hajj_end=date(2027, 5, 20),
    )
    base.update(overrides)
    return Tenant(**base)


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2027, 1, 1), "normal"),
        (date(2027, 2, 8), "ramadan"),   # أول يوم
        (date(2027, 2, 20), "ramadan"),
        (date(2027, 3, 9), "ramadan"),   # آخر يوم
        (date(2027, 3, 10), "normal"),   # العيد
        (date(2027, 5, 15), "hajj"),
        (date(2027, 6, 1), "normal"),
    ],
)
def test_scheduled_season_switches_on_its_own(day, expected):
    assert scheduled_hotel().effective_season(day) == expected


def test_manual_season_wins_when_scheduling_is_off():
    hotel = scheduled_hotel(season_auto=False, season="hajj")
    assert hotel.effective_season(date(2027, 2, 20)) == "hajj"


def test_outside_windows_falls_back_to_normal_not_last_season():
    """بقاء وضع رمضان بعد العيد أخطر من العودة للوضع العادي."""
    hotel = scheduled_hotel(season="ramadan")
    assert hotel.effective_season(date(2027, 4, 1)) == "normal"


def test_incomplete_window_is_ignored():
    hotel = scheduled_hotel(ramadan_end=None)
    assert hotel.effective_season(date(2027, 2, 20)) == "normal"


def test_service_context_uses_effective_season(db):
    from daif.models import Guest
    from daif.service import build_context

    hotel = scheduled_hotel(season="normal", season_auto=True,
                            ramadan_start=date.today(), ramadan_end=date.today())
    db.add(hotel)
    db.flush()
    guest = Guest(tenant_id=hotel.id, wa_id="1", room="402")
    assert build_context(hotel, guest).season == "ramadan"


# --- انتهاء صلاحية الحقائق --------------------------------------------------

@pytest.fixture
def hotel_with_facts(db):
    hotel = Tenant(slug="taibah", name="فندق طيبة")
    db.add(hotel)
    db.flush()
    today = date.today()
    db.add_all([
        Fact(tenant_id=hotel.id, key="K01", text="بلا صلاحية"),
        Fact(tenant_id=hotel.id, key="K02", text="تنتهي بعد ٣ أيام",
             valid_until=today + timedelta(days=3)),
        Fact(tenant_id=hotel.id, key="K03", text="انتهت أمس",
             valid_until=today - timedelta(days=1)),
        Fact(tenant_id=hotel.id, key="K04", text="بعيدة",
             valid_until=today + timedelta(days=90)),
        Fact(tenant_id=hotel.id, key="K05", text="معطّلة ومنتهية",
             valid_until=today - timedelta(days=2), active=False),
    ])
    db.flush()
    return hotel


def test_expiring_lists_near_and_past_only(hotel_with_facts, db):
    keys = [f.key for f in expiring_facts(db, hotel_with_facts.id)]
    assert keys == ["K03", "K02"]  # مرتبة بتاريخ الانتهاء


def test_disabled_facts_are_not_flagged(hotel_with_facts, db):
    assert "K05" not in [f.key for f in expiring_facts(db, hotel_with_facts.id)]


def test_horizon_is_configurable(hotel_with_facts, db):
    assert len(expiring_facts(db, hotel_with_facts.id, days=120)) == 3


# --- التصعيد ---------------------------------------------------------------

class FakeHttp:
    def __init__(self, status=200, raises=None):
        self.status = status
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None):
        if self.raises:
            raise self.raises
        self.calls.append((url, json))

        class R:
            status_code = self.status
            text = ""

        return R()


def test_payload_carries_what_the_duty_manager_needs():
    payload = build_payload("فندق طيبة", "402", "مكيف معطّل", "urgent", "966500000001")
    assert payload["room"] == "402"
    assert payload["hotel"] == "فندق طيبة"
    assert payload["event"] == "urgent_request"


def test_notify_posts_to_the_channel():
    http = FakeHttp()
    assert notify("https://hooks.example.com/duty", {"a": 1}, client=http) is True
    assert http.calls == [("https://hooks.example.com/duty", {"a": 1})]


def test_missing_channel_fails_loudly_not_silently():
    assert notify("", {"a": 1}, client=FakeHttp()) is False


def test_channel_error_does_not_raise():
    import httpx

    assert notify("https://x", {"a": 1}, client=FakeHttp(raises=httpx.ConnectError("down"))) is False
    assert notify("https://x", {"a": 1}, client=FakeHttp(status=500)) is False
