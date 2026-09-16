"""دخول النزيل: من مسح الملصق إلى إغلاق الجلسة عند المغادرة.

المشكلة التي يحلّها هذا الملف
-----------------------------
الملصق المطبوع على جدار الغرفة ثابت ومكشوف. نزيل غادر قبل شهر يجد الرابط في
سجل متصفحه، وأي زائر أو عامل مرّ بالغرفة صوّره. لذلك الرابط **ليس** مفتاحًا،
ولا يصحّ أن يكون.

الترتيب الصحيح ثلاث طبقات، كل واحدة تسدّ ما تتركه التي قبلها:

١. **الإقامة** — لا دخول إلا وفي الغرفة إقامة مفتوحة الآن. وتُفحص عند كل
   رسالة لا عند فتح الجلسة وحدها، لأن المغادرة تقع في منتصف المحادثة.

٢. **ربط الجهاز** — أول دخول في الإقامة يربط الجهاز برمز موقّع عليه هو، فلا
   ينتقل الدخول بنسخ الرابط. وإغلاق الإقامة يقتل كل أجهزتها دفعة واحدة.

٣. **إثبات الحضور أول مرة** — وهي الطبقة التي بدونها ينهار الباقي: بلا إثبات
   يستطيع نزيل قديم أن *يسبق* النزيل الجديد إلى أول ربط في إقامته. وللفندق
   ثلاث طرق يختار منها، وكلها مدعومة:

   - `stay_code`  كود يُطبع على ظرف بطاقة الغرفة عند الوصول. أأمنها — الكود
                  يموت مع الإقامة ولا يتكرر — وأخفّها على النزيل. هو المبدئي.
   - `phone_last4` ملصق ثابت وسؤال واحد: آخر أربعة أرقام من جوالك. بلا طباعة،
                  والنزيل القديم لا يعرف رقم من جاء بعده.
   - `desk_arm`   الاستقبال يفعّل الغرفة عند التسجيل فتُفتح نافذة قصيرة، وأول
                  مسح داخلها يدخل بلا سؤال. أسهلها على النزيل وأثقلها على
                  الموظف.

قاعدة حاكمة: **الشك يُغلق الباب.** كل حالة غامضة هنا ترجع تحدّيًا أو رفضًا،
ولا ترجع جلسة. السماح الخاطئ يعطي غريبًا صوتًا داخل الفندق؛ المنع الخاطئ
يكلّف النزيل سؤالًا واحدًا عند الاستقبال.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import RIYADH, now_riyadh
from .models import Stay, StayDevice

# أنماط إثبات الحضور المدعومة.
Mode = Literal["stay_code", "phone_last4", "desk_arm"]
MODES: tuple[Mode, ...] = ("stay_code", "phone_last4", "desk_arm")

# العائلة الواحدة تدخل من أكثر من جوال. غرفة بعشرين جهازًا ليست عائلة.
MAX_DEVICES = 4

# مهلة بعد ساعة المغادرة المعلنة. النزيل قد يسأل عن سيارة الأجرة وهو في اللوبي،
# وقطع الخدمة عليه عند منتصف النهار بالضبط قسوة بلا فائدة أمنية.
CHECKOUT_GRACE = timedelta(hours=2)

# ساعة المغادرة المعتادة في فنادق المنطقة.
CHECKOUT_HOUR = time(12, 0)

# أقل نافذة تُمنح لإقامة مهما كان تاريخها. تمنع إقامة ميتة عند ولادتها.
MIN_WINDOW = timedelta(hours=1)

# نافذة تفعيل الاستقبال. قصيرة عمدًا: النزيل يصعد لغرفته ويمسح.
ARM_WINDOW = timedelta(minutes=30)


def _aware(moment: datetime) -> datetime:
    """SQLite يعيد التواريخ بلا منطقة زمنية. من قرأ وقتًا من قاعدة البيانات ثم
    مرّره إلينا كان يُسقط المقارنة، فنُسندها كلها إلى توقيت الرياض."""
    return moment if moment.tzinfo else moment.replace(tzinfo=RIYADH)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_device_token() -> str:
    """رمز الجهاز. يُرسل مرة في كوكي ولا يُخزَّن عندنا إلا مجزّأً."""
    return secrets.token_urlsafe(32)


def new_stay_code() -> str:
    """كود الإقامة المطبوع. أحرف بلا لبس — لا صفر ولا حرف O ولا واحد ولا I."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def default_expiry(checkout_on: Optional[datetime | object] = None) -> datetime:
    """الحدّ الأقصى المطلق للإقامة.

    يُحسب من تاريخ المغادرة المحجوز، ويسري **حتى لو** لم يصل إشعار المغادرة:
    نظام إدارة الفنادق قد ينقطع، والموظف قد ينسى. بلا هذا السقف تبقى إقامة
    منسية مفتوحة إلى الأبد، وهذا بالضبط الباب الذي نحاول إغلاقه.
    """
    now = now_riyadh()
    if checkout_on is None:
        return now + timedelta(days=1)
    day = getattr(checkout_on, "date", lambda: checkout_on)()
    due = datetime.combine(day, CHECKOUT_HOUR, tzinfo=RIYADH) + CHECKOUT_GRACE
    # تسجيل وصول بعد ساعة المغادرة المعلنة (إقامة ليوم واحد، أو تأخّر إدخال)
    # كان ينتج إقامة ميتة عند ولادتها: الموظف يسجّل النزيل ثم يخبره الملصق
    # أنه لا توجد إقامة. نضمن نافذة صغيرة حتى لا يقع هذا.
    return max(due, now + MIN_WINDOW)


def is_active(stay: Stay, at: Optional[datetime] = None) -> bool:
    """هل الإقامة تمنح الدخول الآن؟"""
    at = _aware(at or now_riyadh())
    if stay.status != "open" or stay.closed_at is not None:
        return False
    return at < _aware(stay.expires_at)


def active_stay(db: Session, tenant_id: int, room: str,
                at: Optional[datetime] = None) -> Optional[Stay]:
    """الإقامة المفتوحة في هذه الغرفة الآن، إن وُجدت.

    نأخذ الأحدث: لو بقيت إقامة قديمة مفتوحة بالخطأ فالنزيل الحالي أولى بها،
    ولا نريد ربط الجديد بسجلّ من غادر.
    """
    at = _aware(at or now_riyadh())
    rows = db.scalars(
        select(Stay)
        .where(Stay.tenant_id == tenant_id, Stay.room == room, Stay.status == "open")
        .order_by(Stay.opened_at.desc())
    ).all()
    for stay in rows:
        if is_active(stay, at):
            return stay
    return None


def open_stay(db: Session, tenant_id: int, room: str, *, guest_name: str = "",
              phone: str = "", checkout_on=None, mode: Mode = "stay_code") -> Stay:
    """تسجيل وصول: تُغلق أي إقامة سابقة في الغرفة ثم تُفتح واحدة جديدة.

    إغلاق السابقة ليس ترتيبًا للسجلات — هو إبطال فوري لأجهزة من غادر. لحظة
    دخول نزيل جديد هي آخر لحظة يصحّ أن يبقى فيها القديم متصلًا.
    """
    for old in db.scalars(
        select(Stay).where(Stay.tenant_id == tenant_id, Stay.room == room,
                           Stay.status == "open")
    ).all():
        close_stay(db, old, by="pms")

    stay = Stay(
        tenant_id=tenant_id,
        room=room,
        guest_name=guest_name,
        phone_last4=_last4(phone),
        stay_code=new_stay_code() if mode == "stay_code" else "",
        expires_at=default_expiry(checkout_on),
        armed_until=now_riyadh() + ARM_WINDOW if mode == "desk_arm" else None,
    )
    db.add(stay)
    db.flush()
    return stay


def close_stay(db: Session, stay: Stay, *, by: str = "desk") -> None:
    """مغادرة: تنتهي الإقامة وتسقط كل أجهزتها في نفس اللحظة."""
    now = now_riyadh()
    stay.status = "closed"
    stay.closed_at = now
    stay.closed_by = by
    for device in stay.devices:
        if device.revoked_at is None:
            device.revoked_at = now
    db.flush()


def _last4(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else ""


@dataclass(frozen=True)
class Access:
    """نتيجة محاولة دخول.

    `granted` وحده لا يكفي للقارئ: `challenge` يخبر الواجهة بما تطلبه، و
    `reason` يخبر الموظف لماذا مُنع النزيل.
    """

    granted: bool
    stay: Optional[Stay] = None
    token: str = ""                 # يُرسل مرة واحدة، عند الربط فقط
    challenge: str = ""             # stay_code | phone_last4 | desk_arm
    reason: str = ""

    @property
    def needs_proof(self) -> bool:
        return bool(self.challenge)


def _device_of(stay: Stay, token: str) -> Optional[StayDevice]:
    if not token:
        return None
    wanted = _digest(token)
    for device in stay.devices:
        if device.revoked_at is None and hmac.compare_digest(device.token_hash, wanted):
            return device
    return None


def _bind(db: Session, stay: Stay, *, language: str = "", label: str = "") -> Access:
    token = new_device_token()
    db.add(StayDevice(stay_id=stay.id, token_hash=_digest(token),
                      language=language, label=label))
    db.flush()
    db.refresh(stay)
    return Access(granted=True, stay=stay, token=token)


def enter(db: Session, tenant_id: int, room: str, *, mode: Mode = "stay_code",
          device_token: str = "", proof: str = "", language: str = "",
          at: Optional[datetime] = None) -> Access:
    """محاولة دخول من مسح الملصق.

    الترتيب مقصود: نسأل عن الإقامة أولًا، ثم عن الجهاز، ثم عن الإثبات. فجهاز
    مربوط بإقامة انتهت لا يُسأل عن إثبات — يُرفض، لأن السؤال يوحي بأن الباب
    ما زال قائمًا.
    """
    at = _aware(at or now_riyadh())
    stay = active_stay(db, tenant_id, room, at)
    if stay is None:
        return Access(granted=False, reason="no_active_stay")

    device = _device_of(stay, device_token)
    if device is not None:
        device.last_seen_at = at
        if language:
            device.language = language
        db.flush()
        return Access(granted=True, stay=stay)

    live = [d for d in stay.devices if d.revoked_at is None]
    if len(live) >= MAX_DEVICES:
        return Access(granted=False, stay=stay, reason="device_limit")

    # نافذة تفعيل الاستقبال تمرّ بلا سؤال — الموظف فتحها قبل دقائق لهذا النزيل.
    if mode == "desk_arm":
        if stay.armed_until is not None and at < _aware(stay.armed_until):
            return _bind(db, stay, language=language)
        return Access(granted=False, stay=stay, challenge="desk_arm",
                      reason="window_closed")

    expected = stay.stay_code if mode == "stay_code" else stay.phone_last4
    if not expected:
        # الفندق اختار وضعًا بلا بيانات تسنده. لا نفتح الباب لأن الإعداد ناقص.
        return Access(granted=False, stay=stay, challenge=mode, reason="not_configured")

    if not proof:
        return Access(granted=False, stay=stay, challenge=mode, reason="proof_required")

    given = proof.strip().upper() if mode == "stay_code" else _last4(proof)
    if not hmac.compare_digest(given, expected.upper() if mode == "stay_code" else expected):
        return Access(granted=False, stay=stay, challenge=mode, reason="proof_wrong")

    return _bind(db, stay, language=language)


def check(db: Session, tenant_id: int, room: str, device_token: str,
          at: Optional[datetime] = None) -> Access:
    """الفحص المطلوب عند **كل** رسالة، لا عند فتح الجلسة وحدها.

    المغادرة تقع في منتصف المحادثة: نزيل يسأل، يمرّ الموظف بالمغادرة، ثم يرسل
    النزيل رسالة ثانية. بلا هذا الفحص تستمر جلسته إلى ما لا نهاية.
    """
    at = _aware(at or now_riyadh())
    stay = active_stay(db, tenant_id, room, at)
    if stay is None:
        return Access(granted=False, reason="no_active_stay")
    device = _device_of(stay, device_token)
    if device is None:
        return Access(granted=False, stay=stay, reason="device_revoked")
    device.last_seen_at = at
    db.flush()
    return Access(granted=True, stay=stay)


def expire_due(db: Session, tenant_id: int, at: Optional[datetime] = None) -> int:
    """يُشغَّل دوريًا: يقفل كل إقامة تجاوزت سقفها ولم يصل إشعار مغادرتها.

    شبكة الأمان الأخيرة. تعمل بلا تكامل ولا تدخل بشري، وهي التي تضمن أن
    غرفة منسية لا تبقى مفتوحة للأبد.
    """
    at = _aware(at or now_riyadh())
    closed = 0
    for stay in db.scalars(
        select(Stay).where(Stay.tenant_id == tenant_id, Stay.status == "open")
    ).all():
        if not is_active(stay, at):
            close_stay(db, stay, by="expiry")
            closed += 1
    return closed
