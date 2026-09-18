"""التصعيد: طلب عاجل والاستقبال غير مشغّل.

المواصفة تقول إن الطلب العاجل حين لا يكون الاستقبال مشغّلًا يجب أن يصل كتنبيه
هاتفي لا أن ينتظر في طابور. هذه الوحدة تنادي قناة الفندق المسجّلة. الفشل هنا
لا يُسقط الرد على النزيل — الرد أُرسل أصلًا — لكنه يُسجَّل بوضوح.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("daif.escalation")

TIMEOUT = 8.0


def build_payload(tenant_name: str, room: str, detail: str, urgency: str, wa_id: str) -> dict:
    """حمولة مختصرة يقرأها المناوب من إشعار الجوال."""
    return {
        "event": "urgent_request",
        "hotel": tenant_name,
        "room": room,
        "detail": detail,
        "urgency": urgency,
        "guest_wa_id": wa_id,
        "reason": "طلب عاجل والاستقبال غير مشغّل",
    }


def notify(webhook_url: str, payload: dict, *, client: Any | None = None) -> bool:
    """ينادي قناة التصعيد. يعيد True عند النجاح."""
    if not webhook_url.strip():
        logger.error("تصعيد بلا قناة مسجّلة: %s", payload)
        return False
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        response = http.post(webhook_url, json=payload)
        if response.status_code >= 400:
            logger.error("فشل التصعيد %s: %s", response.status_code, response.text[:300])
            return False
        return True
    except httpx.HTTPError as exc:
        logger.exception("تعذّر الوصول لقناة التصعيد: %s", exc)
        return False
    finally:
        if client is None:
            http.close()
