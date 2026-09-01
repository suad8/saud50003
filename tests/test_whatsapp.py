"""اختبارات تكامل واتساب: التوقيع وتحليل الحمولة."""

from __future__ import annotations

import hashlib
import hmac
import json

from daif.whatsapp import parse_webhook, verify_signature

SECRET = "app-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(*messages: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "PN123"},
                            "contacts": [{"wa_id": "966500000001", "profile": {"name": "أحمد"}}],
                            "messages": list(messages),
                        },
                    }
                ],
            }
        ],
    }


def test_valid_signature_accepted():
    body = json.dumps(payload()).encode()
    assert verify_signature(body, sign(body), SECRET) is True


def test_tampered_body_rejected():
    body = json.dumps(payload()).encode()
    assert verify_signature(body + b" ", sign(body), SECRET) is False


def test_missing_secret_rejects_everything():
    """بلا سرّ مضبوط لا نقبل أي حمولة — الافتراض الآمن هو الرفض."""
    body = b"{}"
    assert verify_signature(body, sign(body), "") is False


def test_missing_header_rejected():
    assert verify_signature(b"{}", None, SECRET) is False


def test_text_message_parsed():
    out = parse_webhook(
        payload({"from": "966500000001", "id": "wamid.1", "type": "text",
                 "text": {"body": "وش كلمة سر الواي فاي؟"}})
    )
    assert len(out.messages) == 1
    msg = out.messages[0]
    assert msg.text == "وش كلمة سر الواي فاي؟"
    assert msg.phone_number_id == "PN123"
    assert msg.profile_name == "أحمد"
    assert msg.low_confidence is False


def test_voice_note_flagged_low_confidence():
    """لا نفرّغ الصوت — يُحوَّل لموظف بدل التخمين."""
    out = parse_webhook(
        payload({"from": "966500000002", "id": "wamid.2", "type": "audio", "audio": {"id": "a"}})
    )
    assert out.messages[0].low_confidence is True


def test_image_caption_is_used_as_text():
    out = parse_webhook(
        payload({"from": "966500000003", "id": "wamid.3", "type": "image",
                 "image": {"caption": "المكيف يقطر"}})
    )
    msg = out.messages[0]
    assert msg.text == "المكيف يقطر"
    assert msg.has_media is True
    assert msg.low_confidence is False


def test_reactions_and_statuses_ignored():
    out = parse_webhook(
        payload({"from": "966500000004", "id": "wamid.4", "type": "reaction",
                 "reaction": {"emoji": "👍"}})
    )
    assert out.messages == []


def test_empty_payload_is_safe():
    assert parse_webhook({}).messages == []
