"""استدعاء Claude وتحويل رسالة نزيل إلى رد محكوم بالعقد."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import anthropic

from .config import Settings, get_settings
from .context import GuestContext
from .guardrails import GuardrailResult, enforce
from .knowledge import KnowledgeBase
from .prompt import (
    build_cached_system,
    build_inline_system,
    render_operating_context,
)
from .restricted import RestrictedMatch, screen
from .schema import GuestReply, safe_handoff

logger = logging.getLogger("daif.assistant")


@dataclass
class Usage:
    """استهلاك التوكِنات لرسالة واحدة — أساس تقارير التكلفة في اللوحة."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def cache_hit(self) -> bool:
        return self.cache_read_tokens > 0


@dataclass
class AssistantResult:
    """ناتج معالجة رسالة واحدة، جاهز للإرسال والتخزين."""

    reply: GuestReply
    violations: list[str] = field(default_factory=list)
    escalate: bool = False
    restricted: RestrictedMatch | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    model: str = ""
    request_id: str | None = None
    degraded: bool = False  # صحيح إن فشل النموذج وتم الرجوع لتحويل آمن

    @property
    def should_reply(self) -> bool:
        return bool(self.reply.answer.strip())


class Assistant:
    """المحرّك: فحص مسبق ← نموذج ← حواجز."""

    def __init__(
        self,
        client: Any | None = None,
        settings: Settings | None = None,
        template: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._template = template

    @property
    def client(self) -> Any:
        """يُنشأ العميل عند أول استعمال حتى لا تحتاج الاختبارات مفتاحًا."""
        if self._client is None:
            self._client = anthropic.Anthropic(timeout=self.settings.request_timeout)
        return self._client

    # ------------------------------------------------------------------
    def reply(
        self,
        *,
        ctx: GuestContext,
        kb: KnowledgeBase,
        message: str,
        history: Iterable[dict] | None = None,
        low_confidence_input: bool = False,
    ) -> AssistantResult:
        """يعالج رسالة نزيل واحدة ويعيد ردًا مطابقًا للعقد.

        `low_confidence_input` يُرفع للرسائل الصوتية المفرّغة أو أي مدخل مشكوك
        في قراءته — فيُحوَّل مباشرة دون استدعاء النموذج.
        """
        started = time.monotonic()

        if not message or not message.strip():
            return AssistantResult(
                reply=safe_handoff(
                    "low_confidence",
                    note="رسالة فارغة أو غير نصية — يحتاج موظفًا",
                ),
                degraded=True,
            )

        if low_confidence_input:
            return AssistantResult(
                reply=safe_handoff(
                    "low_confidence",
                    note="رسالة صوتية أو تفريغ منخفض الثقة — يحتاج موظفًا",
                    answer="سيتواصل معك الاستقبال.",
                ),
                degraded=True,
            )

        # ١) الفحص المسبق للمواضيع الممنوعة — يشدّد فقط
        restricted = screen(message)

        facts_block = kb.render(ctx.now, ctx.season)
        system_text = build_cached_system(
            ctx.hotel_name, facts_block, template=self._template, group_mode=ctx.group_mode
        )
        turns: list[dict] = list(history or [])
        turns.append({"role": "user", "content": message})
        turns.append({"role": "system", "content": render_operating_context(ctx)})

        try:
            raw, usage, request_id = self._call(system_text, turns)
        except _OperatorChannelUnsupported:
            # النموذج لا يقبل رسائل المشغّل — نضع السياق داخل البرومبت
            logger.warning("رسائل المشغّل غير مدعومة؛ الرجوع لوضع السياق داخل البرومبت")
            inline = build_inline_system(
                ctx.hotel_name, facts_block, ctx, template=self._template
            )
            try:
                raw, usage, request_id = self._call(inline, turns[:-1])
            except Exception as exc:  # noqa: BLE001
                return self._failure(exc, restricted, started)
        except Exception as exc:  # noqa: BLE001
            return self._failure(exc, restricted, started)

        # ٢) الحواجز
        result: GuardrailResult = enforce(
            raw, ctx, kb, restricted=restricted, settings=self.settings
        )

        return AssistantResult(
            reply=result.reply,
            violations=result.violations,
            escalate=result.escalate,
            restricted=restricted,
            usage=usage,
            latency_ms=int((time.monotonic() - started) * 1000),
            model=self.settings.model,
            request_id=request_id,
        )

    # ------------------------------------------------------------------
    def _call(
        self, system_text: str, turns: list[dict]
    ) -> tuple[GuestReply, Usage, str | None]:
        """نداء واحد للنموذج بمخرجات منظّمة إلزاميًا."""
        try:
            response = self.client.messages.parse(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                # البادئة الثابتة تُخزَّن مؤقتًا: القواعد وقاعدة المعرفة لا تتغير
                # بين الرسائل، فلا يُدفع ثمنها في كل مرة.
                system=[
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=turns,
                output_format=GuestReply,
                output_config={"effort": self.settings.effort},
            )
        except anthropic.BadRequestError as exc:
            if "system" in str(exc).lower() and "role" in str(exc).lower():
                raise _OperatorChannelUnsupported from exc
            raise

        parsed = response.parsed_output
        if parsed is None:
            raise ValueError("النموذج لم يُعد مخرجًا منظّمًا صالحًا")

        raw_usage = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
        )
        return parsed, usage, getattr(response, "_request_id", None)

    # ------------------------------------------------------------------
    def _failure(
        self, exc: Exception, restricted: RestrictedMatch | None, started: float
    ) -> AssistantResult:
        """أي عطل في النموذج يعني تحويلًا لموظف، لا صمتًا ولا تخمينًا."""
        logger.exception("فشل استدعاء النموذج: %s", exc)
        note = "تعذّر تشغيل المساعد — يحتاج موظفًا"
        if restricted is not None:
            note = f"{restricted.note} — يحتاج موظفًا"
        return AssistantResult(
            reply=safe_handoff("low_confidence", note=note),
            violations=[f"عطل في النموذج: {type(exc).__name__}"],
            restricted=restricted,
            latency_ms=int((time.monotonic() - started) * 1000),
            degraded=True,
        )


class _OperatorChannelUnsupported(Exception):
    """النموذج لا يدعم رسائل المشغّل داخل messages."""
