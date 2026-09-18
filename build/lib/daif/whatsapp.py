"""تكامل واتساب (WhatsApp Cloud API): استقبال، تحقق، وإرسال."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .config import Settings, get_settings

logger = logging.getLogger("daif.whatsapp")

GRAPH_BASE = "https://graph.facebook.com"

# أنواع لا تُعالَج آليًا: لا نفرّغ الصوت ولا نقرأ المستندات، فتُحوَّل لموظف.
_LOW_CONFIDENCE_TYPES = {"audio", "voice", "video", "document", "sticker"}
# أنواع تُتجاهل تمامًا
_IGNORED_TYPES = {"reaction", "system", "unsupported"}


@dataclass
class InboundMessage:
    """رسالة واردة بعد تطبيع حمولة Meta."""

    wa_id: str
    message_id: str
    phone_number_id: str
    text: str = ""
    profile_name: str = ""
    kind: str = "text"
    has_media: bool = False
    low_confidence: bool = False
    context_message_id: str = ""

    @property
    def actionable(self) -> bool:
        """هل تستحق ردًا؟ الرسالة الفارغة غير منخفضة الثقة لا تستحق."""
        return bool(self.text.strip()) or self.low_confidence


@dataclass
class WebhookPayload:
    """كل ما استُخلص من نداء webhook واحد."""

    messages: list[InboundMessage] = field(default_factory=list)
    statuses: int = 0


# ---------------------------------------------------------------------------
# التحقق
# ---------------------------------------------------------------------------

def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """يتحقق من ترويسة X-Hub-Signature-256.

    غياب السرّ في الإعدادات يعني رفض كل الطلبات — لا نقبل حمولة غير موثّقة.
    """
    if not app_secret:
        logger.error("WHATSAPP_APP_SECRET غير مضبوط — رُفض نداء webhook")
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :].strip())


def verify_subscription(
    mode: str | None, token: str | None, challenge: str | None, settings: Settings | None = None
) -> str | None:
    """تحقق الاشتراك الأولي (GET). يعيد نص التحدي عند النجاح."""
    cfg = settings or get_settings()
    if mode == "subscribe" and token and cfg.wa_verify_token and token == cfg.wa_verify_token:
        return challenge or ""
    return None


# ---------------------------------------------------------------------------
# تحليل الحمولة
# ---------------------------------------------------------------------------

def _extract_text(message: dict) -> tuple[str, bool]:
    """يستخرج النص القابل للمعالجة ويقول هل معه وسائط."""
    kind = message.get("type", "")

    if kind == "text":
        return str(message.get("text", {}).get("body", "")), False

    if kind == "image":
        caption = str(message.get("image", {}).get("caption", "") or "")
        return caption, True

    if kind == "interactive":
        block = message.get("interactive", {})
        for key in ("button_reply", "list_reply"):
            if key in block:
                return str(block[key].get("title", "")), False
        return "", False

    if kind == "button":
        return str(message.get("button", {}).get("text", "")), False

    if kind == "location":
        loc = message.get("location", {})
        name = loc.get("name") or loc.get("address") or ""
        return str(name), False

    return "", kind in {"audio", "voice", "video", "document", "sticker"}


def parse_webhook(payload: dict[str, Any]) -> WebhookPayload:
    """يحوّل حمولة Meta إلى رسائل مطبَّعة. يتجاهل ما لا يُعالَج."""
    out = WebhookPayload()
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            if value.get("statuses"):
                out.statuses += len(value["statuses"])
            phone_number_id = str(value.get("metadata", {}).get("phone_number_id", ""))

            names: dict[str, str] = {}
            for contact in value.get("contacts", []) or []:
                wa_id = str(contact.get("wa_id", ""))
                names[wa_id] = str(contact.get("profile", {}).get("name", ""))

            for message in value.get("messages", []) or []:
                kind = str(message.get("type", ""))
                if kind in _IGNORED_TYPES:
                    continue
                wa_id = str(message.get("from", ""))
                text, has_media = _extract_text(message)
                low_conf = kind in _LOW_CONFIDENCE_TYPES and not text.strip()
                inbound = InboundMessage(
                    wa_id=wa_id,
                    message_id=str(message.get("id", "")),
                    phone_number_id=phone_number_id,
                    text=text,
                    profile_name=names.get(wa_id, ""),
                    kind=kind,
                    has_media=has_media,
                    low_confidence=low_conf,
                    context_message_id=str(message.get("context", {}).get("id", "")),
                )
                if inbound.actionable:
                    out.messages.append(inbound)
    return out


# ---------------------------------------------------------------------------
# الإرسال
# ---------------------------------------------------------------------------

class WhatsAppClient:
    """عميل إرسال بسيط فوق Graph API.

    ملاحظة اقتصادية: الرد على رسالة نزيل خلال ٢٤ ساعة رسالة خدمة مجانية.
    الرسائل المبادِرة خارج النافذة تحتاج قالبًا معتمدًا وتُحتسب بالتعرفة.
    """

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def _url(self, path: str) -> str:
        return f"{GRAPH_BASE}/{self.settings.wa_api_version}/{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def send_text(self, to: str, body: str, *, reply_to: str = "") -> dict:
        """يرسل رسالة نصية. يعيد رد Graph API."""
        if not body.strip():
            return {}
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        response = self.client.post(
            self._url(f"{self.phone_number_id}/messages"),
            headers=self._headers(),
            json=payload,
        )
        if response.status_code >= 400:
            logger.error("فشل إرسال واتساب %s: %s", response.status_code, response.text[:400])
        response.raise_for_status()
        return response.json()

    def mark_read(self, message_id: str) -> None:
        """يضع علامة «مقروءة» — يطمئن النزيل أن رسالته وصلت."""
        if not message_id:
            return
        try:
            self.client.post(
                self._url(f"{self.phone_number_id}/messages"),
                headers=self._headers(),
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
            )
        except httpx.HTTPError as exc:  # لا يستحق إفشال الرد
            logger.warning("تعذّر وضع علامة مقروءة: %s", exc)


def client_for_tenant(tenant: Any, settings: Settings | None = None) -> WhatsAppClient:
    """ينشئ عميلًا بمفاتيح الفندق نفسه — كل فندق برقمه ورمزه."""
    return WhatsAppClient(
        access_token=tenant.wa_access_token,
        phone_number_id=tenant.wa_phone_number_id,
        settings=settings,
    )
