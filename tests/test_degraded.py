"""وضع الطوارئ — حدوده أهم من قدرته."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from daif.degraded import quote_for


@dataclass
class F:
    key: str
    topic: str
    text: str
    paid: bool = False
    hours: Optional[tuple] = None


WIFI = F("K01", "wifi", "الشبكة Taibah-Guest وكلمة السر welcome2026.")
ZAMZAM = F("K14", "zamzam", "ماء زمزم مجانًا في برّادات كل دور.")
LAUNDRY = F("K10", "laundry", "الثوب ١٥ ريالًا.", paid=True)
BREAKFAST = F("K02", "breakfast", "الإفطار ٥:٣٠–١٠:٣٠.", hours=(330, 630))


def test_quotes_the_single_matching_fact_verbatim():
    q = quote_for("wifi", [WIFI, ZAMZAM])
    assert q is not None
    assert q.text == WIFI.text          # حرفيًا، بلا إعادة صياغة
    assert q.fact_key == "K01"


def test_unknown_shortcut_is_not_answered():
    assert quote_for("spa", [WIFI]) is None


def test_no_matching_fact_hands_off():
    assert quote_for("wifi", [ZAMZAM]) is None


def test_two_matching_facts_hand_off():
    """حقيقتان تعنيان أننا لا نعرف أيهما أراد — والشك يُحوَّل."""
    other = F("K20", "wifi-lobby", "شبكة اللوبي Taibah-Lobby.")
    assert quote_for("wifi", [WIFI, other]) is None


def test_paid_fact_is_never_quoted():
    """السعر يتغيّر، واقتباسه بلا مراجعة يورّط الفندق."""
    assert quote_for("laundry", [LAUNDRY]) is None


def test_timed_fact_is_never_quoted():
    """«مفتوح الآن» حساب لا اقتباس."""
    assert quote_for("breakfast", [BREAKFAST]) is None


def test_non_arabic_guest_gets_the_text_plus_a_notice():
    q = quote_for("wifi", [WIFI], language="id")
    assert WIFI.text in q.text
    assert "بلغة الفندق" in q.text       # لا نترجم، لكن لا نتركه حائرًا
