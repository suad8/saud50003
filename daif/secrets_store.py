"""أسرار المنصة: من البيئة أولًا، ومن قاعدة البيانات حين لا توجد.

لماذا لا نكتفي بمتغيّرات البيئة: اشتراطها قبل أول تشغيل يوقف النشر عند من
يريد أن يرى النظام يعمل قبل أن يضبط شيئًا. ولماذا لا نكتفي بالمولَّد لعمر
العملية: لأنه يسقط جلسات كل الموظفين مع كل إعادة تشغيل، بلا سبب ظاهر لهم.

فالترتيب: متغيّر البيئة يسبق دائمًا، ثم قيمة محفوظة، ثم تُولَّد وتُحفظ مرة
واحدة. وعند التزاحم على أول توليد يفوز واحد ويقرأ الباقون قيمته — لا نريد
خادمين يوقّعان بسرَّين مختلفين.
"""

from __future__ import annotations

import logging
import os
import secrets

from sqlalchemy.exc import IntegrityError, OperationalError

logger = logging.getLogger(__name__)

# الأسرار التي يجوز توليدها وحفظها. المفتاح الذي يشفّر أسرار الفنادق ليس
# منها: حفظه في قاعدة البيانات التي يحميها يُبطل الحماية.
MANAGED = {
    "dashboard": "DAIF_DASHBOARD_SECRET",
    "roomqr": "DAIF_ROOM_SIGNING_KEY",
}

_cache: dict[str, str] = {}


def get(name: str) -> str:
    """السرّ المطلوب. يولّده ويحفظه إن لزم."""
    env_key = MANAGED.get(name)
    if env_key:
        from_env = os.environ.get(env_key, "").strip()
        if from_env:
            return from_env

    if name in _cache:
        return _cache[name]

    value = _load_or_create(name)
    _cache[name] = value
    return value


def _load_or_create(name: str) -> str:
    from .db import session_scope
    from .models import PlatformSecret

    try:
        with session_scope() as session:
            row = session.get(PlatformSecret, name)
            if row is not None:
                return row.value

            fresh = secrets.token_urlsafe(48)
            session.add(PlatformSecret(name=name, value=fresh))
            try:
                session.flush()
            except IntegrityError:
                # سبقنا خادم آخر إلى الإنشاء — نقرأ قيمته بدل أن نكتب فوقها.
                session.rollback()
                row = session.get(PlatformSecret, name)
                if row is not None:
                    return row.value
                raise
            logger.info(
                "وُلّد سرّ المنصة «%s» وحُفظ. اضبط %s لتثبيته خارج قاعدة البيانات.",
                name, MANAGED.get(name, "-"))
            return fresh
    except OperationalError:
        # قاعدة البيانات مقفلة الآن — وهو وارد على SQLite حين يكون للطلب
        # معاملة كتابة مفتوحة ونفتح نحن اتصالًا ثانيًا لنحفظ سرًّا وُلّد
        # لتوّه. إفشال تسجيل دخول لأننا لم نستطع *حفظ* السرّ أسوأ من
        # استعماله بلا حفظ: نولّده لهذا التشغيل ونحاول الحفظ لاحقًا.
        logger.warning(
            "تعذّر حفظ سرّ المنصة «%s» — يُستعمل مولَّدًا لهذا التشغيل. "
            "اضبط %s ليثبت عبر إعادات التشغيل.",
            name, MANAGED.get(name, "-"), exc_info=True)
        return secrets.token_urlsafe(48)


def warm() -> None:
    """يولّد الأسرار المدارة ويحفظها قبل أول طلب.

    الحفظ أثناء طلب يفتح اتصالًا ثانيًا بينما معاملة الطلب قائمة. تسخينها
    عند الإقلاع يجعل كل قراءة بعدها إصابةَ ذاكرة، فلا كتابة أصلًا.
    """
    for name in MANAGED:
        try:
            get(name)
        except Exception:  # pragma: no cover - التسخين لا يمنع الإقلاع أبدًا
            logger.warning("تعذّر تسخين سرّ المنصة «%s»", name, exc_info=True)


def reset_cache() -> None:
    """للاختبارات: ينسى ما قُرئ حتى يُقرأ من جديد."""
    _cache.clear()
