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


def startup_error() -> str:
    """خطأ سجّله سكربت الإقلاع قبل أن يقوم الخادم (فشل ترحيلات مثلًا)."""
    path = os.environ.get("STARTUP_ERROR_FILE", "")
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# مرجع منصّة كُتب في خانة الاسم بدل خانة القيمة. Railway وأمثالها تأخذ
# الاسم حرفيًا، فينشأ متغيّر اسمه "${{ Postgres.DATABASE_URL }}" لا يقرؤه
# أحد، ويظنّ الناشر أنه ربط قاعدة البيانات. غلط شائع وصامت تمامًا.
REFERENCE_MARKERS = ("${{", "}}", "${")


def misplaced_references() -> list[str]:
    """أسماء متغيّرات تبدو مراجع منصّة وُضعت في خانة الاسم.

    نرجع الأسماء فقط ولا نلمس القيم: صفحة تشخيص عامة لا يجوز أن تطبع سرًّا.
    """
    return sorted(
        name for name in os.environ
        if any(marker in name for marker in REFERENCE_MARKERS)
    )


def describe_value(raw: str) -> str:
    """وصف آمن لقيمة لا تُقرأ: شكلها بلا كشف كلمة المرور.

    التخمين هنا يكلّف جولة نشر كاملة في كل مرة. والقيمة نفسها لا يجوز
    طباعتها — فيها كلمة مرور قاعدة البيانات. فنطبع ما يكفي للتشخيص ولا
    يكفي للدخول: الطول، وما قبل «://» وهو اسم المشغّل لا سرّ فيه، وعلامات
    الأعطال الشائعة.
    """
    notes: list[str] = []
    notes.append(f"طولها {len(raw)} محرفًا")

    # ما قبل «://» اسم مشغّل لا سرّ فيه فنطبعه. وما لا يحوي «://» قد يكون
    # كلمة المرور وحدها لُصقت هنا — فلا نطبع منه حرفًا واحدًا.
    if "://" in raw:
        notes.append(f"وتبدأ بـ «{raw.split('://', 1)[0]}»")

    if raw != raw.strip():
        notes.append("وفيها مسافة أو سطر جديد في أولها أو آخرها")
    if raw[:1] in "\"'" or raw[-1:] in "\"'":
        notes.append("ومحاطة بعلامات اقتباس — احذفها، المنصّة لا تحتاجها")
    if "\n" in raw.strip() or "\r" in raw:
        notes.append("وفيها سطر جديد بداخلها")
    if "://" not in raw:
        notes.append("ولا تحوي «://» أصلًا — فهي ليست عنوانًا")
    if "=" in raw.split("://", 1)[0]:
        notes.append("وفيها «=» قبل «://» — يبدو أنك لصقت السطر كاملًا "
                     "(الاسم=القيمة) في خانة القيمة؛ القيمة وحدها تكفي")
    if " " in raw.strip():
        notes.append("وفيها مسافة بداخلها")

    return "، ".join(notes) + "."


def _parses_as_url(url: str) -> bool:
    """هل يقرأ SQLAlchemy هذا عنوانًا؟ نسأله هو لا نخمّن بتعبير نمطي."""
    try:
        from sqlalchemy.engine.url import make_url

        make_url(url)
    except Exception:
        return False
    return True


def check(*, production: bool | None = None) -> list[Problem]:
    production = is_production() if production is None else production
    found: list[Problem] = []

    # ما فشل قبل الإقلاع يسبق كل شيء: بلا قاعدة بيانات مهيّأة لا معنى لبقية
    # الفحوص، والناشر يحتاج أن يرى هذا أولًا.
    early = startup_error()
    if early:
        found.append(Problem("قاعدة البيانات", early, fatal=True))

    def need(key: str, detail: str) -> None:
        if not os.environ.get(key, "").strip():
            found.append(Problem(key, detail, fatal=production))

    # هذان لم يعودا يمنعان الإقلاع: المنصة تولّدهما وتحفظهما إن غابا، فتعمل
    # بلا إعداد. لكن ضبطهما أقوى — تسريب قاعدة البيانات لا يكشفهما — ويظل
    # تنبيهًا قائمًا حتى يُضبطا.
    if not os.environ.get("DAIF_SECRET_KEY", "").strip():
        found.append(Problem(
            "DAIF_SECRET_KEY",
            "غير مضبوط — تُولَّد أسرار المنصة وتُحفظ في قاعدة البيانات. يعمل، "
            "لكن ضبطه يبقيها خارجها. ولاحقًا: أسرار الفنادق المخزَّنة تحتاجه.",
            fatal=False))
    if not os.environ.get("DAIF_DASHBOARD_SECRET", "").strip():
        found.append(Problem(
            "DAIF_DASHBOARD_SECRET",
            "غير مضبوط — يُولَّد ويُحفظ. الجلسات تصمد أمام إعادة التشغيل.",
            fatal=False))

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        # ليس قاتلًا: المساعد يتحوّل للموظف بدله، وهو سلوك مقصود لا عطل.
        found.append(Problem(
            "ANTHROPIC_API_KEY",
            "غير مضبوط — المساعد سيحوّل كل سؤال لموظف بدل أن يجيب.",
            fatal=False,
        ))

    # نستعمل نفس دالة الاكتشاف التي يستعملها التطبيق، لا قراءة متغيّر واحد:
    # فحص يقرأ اسمًا والتطبيق يقرأ آخر = إما منْع إقلاع سليم أو السماح بمعطوب.
    from .config import PG_URL_ENV_NAMES, discovered_database_url

    # نفحص هذا قبل قاعدة البيانات: حين يقع، يكون هو السبب الحقيقي لغيابها،
    # ورسالة «ما لقيت عنوانًا» وحدها تترك الناشر يعيد نفس الخطأ.
    for name in misplaced_references():
        found.append(Problem(
            "خانة الاسم",
            f"عندك متغيّر اسمه: {name} — هذا مرجع منصّة كُتب في خانة الاسم "
            "بدل خانة القيمة، فما يقرؤه أحد. احذفه، وأنشئ متغيّرًا اسمه "
            "DATABASE_URL وضع المرجع في خانة القيمة.",
            fatal=False,
        ))

    url = discovered_database_url()

    # عنوان موجود لكنه لا يُقرأ: أشيع سببه مرجع منصّة لم يُحلّ، فتُحفظ قيمته
    # نصًّا كما كُتبت. بلا هذا الفحص يصل الناشر إلى أثر تتبّع من SQLAlchemy
    # داخل ترحيلات ألمبك — صحيح ولا يدلّ على المتغيّر ولا على إصلاحه.
    if url and not url.startswith("sqlite"):
        unresolved = any(marker in url for marker in REFERENCE_MARKERS)
        if unresolved:
            found.append(Problem(
                "DATABASE_URL",
                "قيمته نصّ مرجع لم تحلّه المنصّة، فلا يصلح عنوانًا. غالبًا "
                "لوجود مسافات داخل القوسين أو علامات اقتباس حوله: اكتبه "
                "ملتصقًا بلا مسافات ولا اقتباس.",
                fatal=True,
            ))
        elif not _parses_as_url(url):
            found.append(Problem(
                "DATABASE_URL",
                "قيمته موجودة لكن SQLAlchemy لا يقرؤها عنوانًا. "
                f"القيمة التي وصلتنا: {describe_value(url)} "
                "والشكل المتوقَّع: postgresql://مستخدم:كلمة@مضيف:5432/قاعدة",
                fatal=True,
            ))

    if production and (not url or url.startswith("sqlite")):
        names = "، ".join(PG_URL_ENV_NAMES)
        found.append(Problem(
            "DATABASE_URL",
            "الإنتاج على SQLite يفقد كل البيانات مع كل نشر — القرص مؤقت. "
            f"بحثنا عن: {names} (وعن PGHOST/PGDATABASE/PGUSER) فما وجدنا شيئًا.",
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


def diagnostic_html(problems: list[Problem]) -> str:
    """صفحة تشرح العطل بدل خادم يطيح بصمت.

    «Application failed to respond» لا تقول شيئًا. ومن ينشر أول مرة لا يعرف
    أين سجل النشر، ولا أن العطل في متغيّر واحد ناقص. فالخادم الذي لا يستطيع
    أن يخدم التطبيق يبقى قائمًا ليقول لماذا — هذا أنفع من موت صامت.
    """
    rows = []
    # القاتل أولًا لأنه وحده يمنع الإقلاع، ثم المرجع في خانة الاسم لأنه سببه
    # غالبًا، ثم بقية الملاحظات. ترتيب يقرؤه الناشر من أعلى لأسفل فيصلح.
    def _rank(item: Problem) -> int:
        if item.fatal:
            return 0
        return 1 if item.key == "خانة الاسم" else 2

    for p in sorted(problems, key=_rank):
        tone = "bad" if p.fatal else "warn"
        mark = "✕" if p.fatal else "!"
        rows.append(
            f'<li class="{tone}"><span class="m">{mark}</span>'
            f'<div><code>{_esc(p.key)}</code><p>{_esc(p.detail)}</p></div></li>'
        )
    fatal = [p for p in problems if p.fatal]
    head = ("الإعداد ناقص — الخادم قائم والتطبيق موقوف" if fatal
            else "الخادم يعمل، ومعه ملاحظات")
    # مرجع في خانة الاسم يسبق كل شرح آخر: هو سبب غياب قاعدة البيانات، وشرحُ
    # «كيف تربطها» وحده يقود الناشر لإعادة نفس الخطأ حرفيًا.
    # أوّل قاتل ليس بالضرورة أنفعهم شرحًا: فشل الترحيلات يسبق DATABASE_URL
    # في القائمة، وهو نتيجته لا سببه — فكان يُعرض الشرح العام بينما الشرح
    # المفيد موجود. نختار أخصّ شرح متاح، لا أوّل قاتل.
    fix = ""
    for key in ("خانة الاسم", "DATABASE_URL"):
        if any(p.key == key for p in problems) and key in FIXES:
            fix = FIXES[key]
            break
    else:
        if fatal:
            fix = next((FIXES[p.key] for p in fatal if p.key in FIXES), DEFAULT_FIX)

    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ضيف — فحص الإعداد</title><style>
:root{{color-scheme:light dark;--bg:#f4f7fc;--card:#fff;--ink:#0a1a2f;--ink2:#5a6b85;
--line:#dde5f2;--bad:#c02626;--bad-s:#fdeceb;--warn:#a55a00;--warn-s:#fff4e2;--blue:#1257e0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#070d17;--card:#0f1725;--ink:#e8eefb;
--ink2:#94a3bd;--line:#1e2a3f;--bad:#f28b85;--bad-s:#2e1616;--warn:#e0a244;--warn-s:#2a2011;
--blue:#5b9bff}}}}
*{{box-sizing:border-box;margin:0}}
body{{background:var(--bg);color:var(--ink);direction:rtl;padding:24px 16px;line-height:1.65;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
.w{{max-width:640px;margin:0 auto}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;
margin-bottom:14px}}
h1{{font-size:1.3rem;margin-bottom:8px}}
.lede{{color:var(--ink2);font-size:.94rem}}
ul{{list-style:none;padding:0;margin:18px 0 0;display:flex;flex-direction:column;gap:10px}}
li{{display:flex;gap:11px;padding:13px 15px;border-radius:12px;align-items:flex-start}}
li.bad{{background:var(--bad-s)}} li.warn{{background:var(--warn-s)}}
li .m{{font-weight:700;flex:none;width:20px;text-align:center}}
li.bad .m{{color:var(--bad)}} li.warn .m{{color:var(--warn)}}
code{{font-family:ui-monospace,monospace;font-size:.86rem;font-weight:600}}
li p{{font-size:.88rem;color:var(--ink2);margin-top:3px}}
h2{{font-size:1rem;margin-bottom:10px}}
pre{{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:13px;
overflow-x:auto;font-size:.82rem;direction:ltr;text-align:left}}
.note{{font-size:.84rem;color:var(--ink2);margin-top:12px}}
</style></head><body><div class="w">
<div class="c"><h1>{head}</h1>
<p class="lede">هذي صفحة فحص من ضيف نفسه، مو خطأ من الاستضافة.
لمّا يكون الإعداد ناقصًا، يبقى الخادم قائمًا ليقول السبب بدل أن يسقط بصمت.</p>
<ul>{''.join(rows)}</ul></div>
{fix}
<p class="note">بعد ما تضبطها، أعد النشر. وللفحص من جهازك:
<code>python scripts/smoke.py https://نطاقك</code></p>
</div></body></html>"""


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


DEFAULT_FIX = """<div class="c"><h2>كيف تضبطها</h2>
<p class="lede">من Variables في الاستضافة، أضف المتغيّرات المعلَّمة بـ ✕ ثم أعد النشر.</p></div>"""

FIXES = {
    "خانة الاسم": """<div class="c"><h2>المرجع في الخانة الغلط</h2>
<p>أُنشئ متغيّر <b>اسمه</b> هو المرجع نفسه. Railway تأخذ الاسم حرفيًا، فما
صار هناك متغيّر باسم <code>DATABASE_URL</code> يقرؤه التطبيق.</p>
<p><b>الصح:</b> احذف ذاك المتغيّر، ثم <b>+ New Variable</b>:</p>
<pre>الاسم  :  DATABASE_URL
القيمة :  ${{Postgres.DATABASE_URL}}</pre>
<p>بلا مسافات داخل القوسين وبلا علامات اقتباس — المسافة تمنع المنصّة من حلّ المرجع فتُحفظ القيمة نصًّا كما كُتبت.</p>
<p>خانتان منفصلتان: الاسم على اليسار والقيمة على اليمين. المرجع يدخل في
<b>القيمة</b> وحدها.</p>
<p>وللتأكّد بعدها: اضغط أيقونة العين 👁 جنب <code>DATABASE_URL</code>. لازم
تظهر لك جملة اتصال حقيقية تبدأ بـ <code>postgresql://</code>. لو ظهر لك نصّ
المرجع كما هو، فاسم خدمة قاعدة البيانات داخل الأقواس لا يطابق اسمها عندك.</p>
</div>""",

    "DATABASE_URL": """<div class="c"><h2>كيف تربط قاعدة البيانات</h2>
<p class="lede">إضافة Postgres للمشروع لا تصله بالتطبيق تلقائيًا — لا بد من ربط صريح.
هذي أشيع خطوة تُنسى في أول نشر.</p>
<p class="lede" style="margin-top:12px"><b>في Railway:</b> افتح خدمة التطبيق ←
<b>Variables</b> ← <b>+ New Variable</b> ← اسمه <code>DATABASE_URL</code> وقيمته:</p>
<pre>${{Postgres.DATABASE_URL}}</pre>
<p><b>ملتصقة بلا مسافات داخل القوسين، وبلا علامات اقتباس.</b> المسافة تمنع المنصّة من حلّ المرجع، فتُحفظ القيمة نصًّا فلا تصلح عنوانًا.</p>
<p class="lede" style="margin-top:10px">(اسم <code>Postgres</code> هو اسم خدمة قاعدة
البيانات عندك كما يظهر في اللوحة.) ثم أعد النشر.</p>
<p class="note">أو للتجربة السريعة: احذف <code>DAIF_ENV</code> فيعمل على قاعدة محلية —
لكن قرص الاستضافة مؤقت، فالبيانات تضيع مع كل نشر.</p></div>""",
}
