"""الحواجز: تطبيق القواعد المطلقة على مخرجات النموذج، برمجيًا.

القاعدة الحاكمة لهذا الملف كله: **الحواجز تشدّد فقط.**
لا يوجد مسار واحد هنا يحوّل تحويلًا إلى جواب، أو يرفع الثقة، أو يعيد تذكرة
أُلغيت. أسوأ ما يفعله خطأ في هذا الملف هو إحالة زائدة إلى موظف بشري.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .clock import parse_iso8601, to_riyadh
from .config import Settings, get_settings
from .context import GuestContext
from .knowledge import KnowledgeBase
from .restricted import RestrictedMatch
from .schema import (
    HOUSEKEEPING_TYPES,
    GuestReply,
    Handoff,
    HandoffReason,
    HandoffTo,
)

# فواصل الجمل عبر اللغات المتوقعة: لاتيني، عربي، أردي، بنغالي/شرق آسيوي
_SENTENCE_CHARS = ".!?؟۔।…"
_DECIMAL_DOT = re.compile(r"(?<=\d)\.(?=\d)")
_SENT_SPLIT = re.compile(rf"[^{re.escape(_SENTENCE_CHARS)}\n]+[{re.escape(_SENTENCE_CHARS)}]*")
_LANG_RE = re.compile(r"^[a-z]{2}$")
_LANG_TAG_RE = re.compile(r"^([a-z]{2})[-_][a-z]{2,4}$")

# النموذج قد يكتب اسم اللغة بدل رمزها. الأسماء المعروفة تُطبَّع بدل أن تُرفض —
# رمز خاطئ لا يستحق إحالة نزيل إلى موظف. ما لا يُعرف يُحوَّل.
_LANG_ALIASES = {
    "arabic": "ar", "عربي": "ar", "العربية": "ar",
    "english": "en", "indonesian": "id", "bahasa": "id", "bahasa indonesia": "id",
    "malay": "ms", "bahasa melayu": "ms", "turkish": "tr", "türkçe": "tr",
    "urdu": "ur", "اردو": "ur", "bengali": "bn", "bangla": "bn",
    "persian": "fa", "farsi": "fa", "french": "fr", "français": "fr",
    "hausa": "ha",
}


def normalize_language(raw: str | None) -> str | None:
    """يعيد رمز ISO 639-1 من رمز أو اسم لغة، أو None إن تعذّر."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    if _LANG_RE.match(text):
        return text
    tag = _LANG_TAG_RE.match(text)
    if tag:
        return tag.group(1)
    return _LANG_ALIASES.get(text)
_PLACEHOLDER = "\x01"


def split_sentences(text: str) -> list[str]:
    """يقسم النص إلى جمل دون كسر الأرقام العشرية (3.5) أو الأوقات (5:30)."""
    guarded = _DECIMAL_DOT.sub(_PLACEHOLDER, text)
    pieces = _SENT_SPLIT.findall(guarded)
    return [p.replace(_PLACEHOLDER, ".").strip() for p in pieces if p.strip()]


def trim_sentences(text: str, limit: int) -> tuple[str, bool]:
    """يقصّ النص إلى `limit` جملة. يعيد (النص، هل قُصّ)."""
    sentences = split_sentences(text)
    if len(sentences) <= limit:
        return text.strip(), False
    return " ".join(sentences[:limit]), True


@dataclass
class GuardrailResult:
    """نتيجة تمرير رد النموذج على الحواجز."""

    reply: GuestReply
    violations: list[str] = field(default_factory=list)
    escalate: bool = False

    @property
    def clean(self) -> bool:
        return not self.violations


def _force_handoff(
    reply: GuestReply,
    reason: HandoffReason,
    to: HandoffTo,
    note: str,
    answer: str | None = None,
) -> GuestReply:
    """يفرض التحويل ويلغي أي تذكرة. لا يعيد تفعيل جواب أُلغي."""
    return reply.model_copy(
        update={
            "in_scope": False,
            "sources": [],
            "request": None,
            "answer": answer if answer is not None else reply.answer,
            "handoff": Handoff(reason=reason, to=to, note=note),
        }
    )


def enforce(
    reply: GuestReply,
    ctx: GuestContext,
    kb: KnowledgeBase,
    *,
    restricted: RestrictedMatch | None = None,
    settings: Settings | None = None,
) -> GuardrailResult:
    """يطبّق كل القواعد القابلة للتحقق برمجيًا على رد النموذج."""
    cfg = settings or get_settings()
    violations: list[str] = []
    escalate = False

    # ---- ١) الثقة داخل المدى الصحيح ----
    confidence = reply.confidence
    if not (0.0 <= confidence <= 1.0):
        violations.append(f"ثقة خارج المدى: {confidence}")
        confidence = max(0.0, min(1.0, confidence))
        reply = reply.model_copy(update={"confidence": confidence})

    # ---- ٢) رمز اللغة ----
    language = normalize_language(reply.language)
    if language is None:
        violations.append(f"رمز لغة غير صالح: {reply.language!r}")
        reply = _force_handoff(
            reply.model_copy(update={"language": "ar"}),
            "low_confidence",
            "front_desk",
            "تعذّر تحديد لغة النزيل — يحتاج موظفًا",
            answer="سيتواصل معك الاستقبال.",
        )
    elif language != reply.language:
        reply = reply.model_copy(update={"language": language})

    # ---- ٣) رد فارغ ----
    if not reply.answer.strip():
        violations.append("رد فارغ")
        reply = _force_handoff(
            reply,
            "low_confidence",
            "front_desk",
            "النموذج لم يُنتج ردًا — يحتاج موظفًا",
            answer="سيتواصل معك الاستقبال.",
        )

    # ---- ٤) موضوع ممنوع التقطه الفحص المسبق (القاعدة ٢) ----
    if restricted is not None:
        if reply.handoff is None or reply.in_scope:
            violations.append(f"موضوع ممنوع لم يُحوَّل: {restricted.category}")
        reply = _force_handoff(
            reply,
            "restricted_topic",
            "front_desk",
            f"{restricted.note} — «{restricted.evidence}»",
            answer=reply.answer if reply.handoff else "سيوافيك الاستقبال بذلك.",
        )

    # ---- ٥) شكوى (القاعدة ٤): لا جواب، ولا تذكرة، ومدير الوردية ----
    if reply.intent == "complaint":
        note = reply.handoff.note if reply.handoff else ""
        if restricted is not None:
            note = f"{note} | {restricted.note}".strip(" |")
        if reply.request is not None or reply.in_scope:
            violations.append("شكوى عولجت كطلب أو كجواب")
        reply = _force_handoff(
            reply,
            "complaint",
            "duty_manager",
            note or "شكوى من نزيل — تحتاج تواصلًا بشريًا",
            answer=reply.answer,
        )

    # ---- ٦) المصادر: كل معرّف يجب أن يوجد فعلًا في قاعدة المعرفة ----
    known = kb.ids
    active = kb.active_ids(ctx.now, ctx.season)
    kept: list[str] = []
    for source in reply.sources:
        sid = source.strip()
        if sid in active:
            kept.append(sid)
        elif sid in known:
            violations.append(f"استشهاد بحقيقة غير صالحة هذا الموسم/التاريخ: {sid}")
        else:
            violations.append(f"استشهاد بمعرّف غير موجود: {sid}")
    if kept != reply.sources:
        reply = reply.model_copy(update={"sources": kept})

    # جواب داخل النطاق بلا تذكرة يجب أن يحمل مصدرًا واحدًا على الأقل
    if reply.in_scope and reply.request is None and not reply.sources:
        violations.append("جواب داخل النطاق بلا مصدر موثّق")
        reply = _force_handoff(
            reply,
            "no_documented_answer",
            "front_desk",
            "سؤال بلا حقيقة موثّقة تغطيه",
            answer="سيوافيك الاستقبال بذلك.",
        )

    # ---- ٧) الغرفة (القاعدة ٥): لا تذكرة إلا على غرفة مصرّح بها ----
    #
    # قائمة الغرف المصرّح بها تأتي من سجلات الفندق دائمًا: غرفة النزيل في
    # الوضع الفردي، وغرف المجموعة المسجّلة في وضع المطوّف. لا تُقبل غرفة
    # مصدرها نص الرسالة مهما بدت صحيحة.
    if reply.request is not None:
        allowed = ctx.authorised_rooms
        requested = reply.request.all_rooms
        if not allowed:
            violations.append("تذكرة لرقم غير مربوط بغرفة")
            reply = _force_handoff(
                reply,
                "unverified_room",
                "front_desk",
                "طلب من رقم غير مربوط بغرفة — يحتاج تحقق",
                answer="سيتواصل معك الاستقبال لتأكيد الطلب.",
            )
        else:
            kept = [room for room in requested if room in allowed]
            rejected = [room for room in requested if room not in allowed]
            if not kept:
                violations.append(
                    f"تذكرة لغرف غير مصرّح بها: {rejected!r} خارج {list(allowed)!r}"
                )
                reply = _force_handoff(
                    reply,
                    "unverified_room",
                    "front_desk",
                    f"طلب لغرفة {', '.join(rejected) or '؟'} غير مربوطة بهذا الرقم — يحتاج تحقق",
                    answer="سيتواصل معك الاستقبال لتأكيد الطلب.",
                )
            else:
                detail = reply.request.detail
                if rejected:
                    violations.append(f"أُسقطت غرف غير مصرّح بها: {rejected!r}")
                    detail = f"{detail} — [أُسقطت غرف غير مصرّح بها: {', '.join(rejected)}]"
                if rejected or kept != requested or reply.request.rooms != kept[1:]:
                    reply = reply.model_copy(
                        update={
                            "request": reply.request.model_copy(
                                update={"room": kept[0], "rooms": kept[1:], "detail": detail}
                            )
                        }
                    )

    # ---- ٨) عتبة الثقة ----
    if reply.confidence < cfg.confidence_threshold:
        if reply.in_scope or reply.handoff is None:
            violations.append(f"ثقة دون العتبة: {reply.confidence}")
        reply = _force_handoff(
            reply,
            reply.handoff.reason if reply.handoff else "low_confidence",
            reply.handoff.to if reply.handoff else "front_desk",
            reply.handoff.note if reply.handoff else "ثقة منخفضة — يحتاج موظفًا",
            answer=reply.answer,
        )

    # ---- ٩) التحويل والتذكرة لا يجتمعان ----
    if reply.handoff is not None and reply.request is not None:
        violations.append("تحويل وتذكرة معًا — أُلغيت التذكرة")
        reply = reply.model_copy(update={"request": None})

    # ---- ١٠) الوقت المطلوب داخل نافذة التدبير ----
    if reply.request is not None and reply.request.requested_time:
        when = parse_iso8601(reply.request.requested_time)
        window = ctx.hk_window
        drop_reason = ""
        if when is None:
            drop_reason = "صيغة وقت غير صالحة"
        elif when < to_riyadh(ctx.now):
            drop_reason = "وقت في الماضي"
        elif (
            reply.request.type in HOUSEKEEPING_TYPES
            and window is not None
            and not window.contains(when)
        ):
            drop_reason = f"وقت خارج نافذة التدبير {window}"
        if drop_reason:
            violations.append(f"{drop_reason}: {reply.request.requested_time}")
            detail = f"{reply.request.detail} — [أُلغي الوقت المطلوب: {drop_reason}]"
            reply = reply.model_copy(
                update={
                    "request": reply.request.model_copy(
                        update={"requested_time": None, "detail": detail}
                    )
                }
            )

    # ---- ١١) التصعيد حين يكون الاستقبال غير مشغّل ----
    if (
        reply.request is not None
        and reply.request.urgency == "urgent"
        and ctx.desk_status == "unstaffed"
    ):
        escalate = True

    # ---- ١٢) حد الجمل الثلاث ----
    trimmed, was_trimmed = trim_sentences(reply.answer, cfg.max_sentences)
    if was_trimmed:
        violations.append(f"تجاوز {cfg.max_sentences} جمل — قُصّ الرد")
        reply = reply.model_copy(update={"answer": trimmed})

    # ---- ١٣) اتساق نهائي: خارج النطاق يعني وجود جهة تحويل ----
    if not reply.in_scope and reply.handoff is None:
        violations.append("خارج النطاق بلا جهة تحويل")
        reply = _force_handoff(
            reply,
            "no_documented_answer",
            "front_desk",
            "سؤال خارج نطاق معلومات الفندق",
            answer=reply.answer,
        )
    if not reply.in_scope and reply.sources:
        reply = reply.model_copy(update={"sources": []})

    return GuardrailResult(reply=reply, violations=violations, escalate=escalate)
