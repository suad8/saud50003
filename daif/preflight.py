"""فحص ما قبل الإقلاع — يمنع نشرًا يبدو ناجحًا وهو معطوب.

أخطر نوع من أخطاء النشر ليس الذي يُسقط الخادم، بل الذي يتركه يعمل ويبدو
سليمًا. مثاله الحيّ هنا: بلا `DAIF_DASHBOARD_SECRET` يولّد التطبيق سرًّا
لعمر العملية، فيشتغل كل شيء — حتى أول إعادة تشغيل، فتسقط جلسات كل الموظفين
بلا سبب ظاهر. ومع أكثر من نسخة يفشل الدخول عشوائيًا حسب أي نسخة ردّت.

لذلك في الإنتاج نرفض الإقلاع. الخادم الذي لا يقوم يُرى في دقيقة؛ والخادم
الذي يعمل بنصف إعداد يُكتشف بعد أسبوع من شكاوى لا تُفسَّر.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    key: str
    detail: str
    fatal: bool


def is_production() -> bool:
    """الإنتاج يُعلَن صراحةً، لا يُستنتج.

    الاستنتاج من وجود Postgres أو من اسم المضيف يخطئ في الاتجاهين: يوقف
    تجربة محلية، أو يمرّر نشرًا ناقصًا. والإعلان الصريح لا يخطئ.
    """
    return os.environ.get("DAIF_ENV", "").strip().lower() in {"production", "prod"}


def check(*, production: bool | None = None) -> list[Problem]:
    production = is_production() if production is None else production
    found: list[Problem] = []

    def need(key: str, detail: str) -> None:
        if not os.environ.get(key, "").strip():
            found.append(Problem(key, detail, fatal=production))

    need("DAIF_SECRET_KEY",
         "يشفّر أسرار الفنادق ويوقّع أكواد الغرف. بلا ثابت منه تصير كل "
         "الملصقات المطبوعة غير صالحة عند إعادة التشغيل.")
    need("DAIF_DASHBOARD_SECRET",
         "يوقّع جلسات اللوحة. بلا ثابت منه يخرج كل الموظفين عند كل إعادة تشغيل.")

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        # ليس قاتلًا: المساعد يتحوّل للموظف بدله، وهو سلوك مقصود لا عطل.
        found.append(Problem(
            "ANTHROPIC_API_KEY",
            "غير مضبوط — المساعد سيحوّل كل سؤال لموظف بدل أن يجيب.",
            fatal=False,
        ))

    url = os.environ.get("DAIF_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if production and (not url or url.startswith("sqlite")):
        found.append(Problem(
            "DATABASE_URL",
            "الإنتاج على SQLite يفقد كل البيانات مع كل نشر — القرص مؤقت.",
            fatal=True,
        ))

    if production and not os.environ.get("WHATSAPP_APP_SECRET", "").strip():
        found.append(Problem(
            "WHATSAPP_APP_SECRET",
            "غير مضبوط — تحقّق توقيع واتساب معطّل. اتركه فارغًا فقط إن كانت "
            "قناة واتساب غير مستعملة.",
            fatal=False,
        ))

    return found


def summary(problems: list[Problem]) -> str:
    if not problems:
        return "الإعداد مكتمل."
    lines = []
    for p in problems:
        lines.append(f"{'✖' if p.fatal else '▲'} {p.key}: {p.detail}")
    return "\n".join(lines)


class ConfigurationError(RuntimeError):
    """إعداد ناقص يمنع الإقلاع في الإنتاج."""
