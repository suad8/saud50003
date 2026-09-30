"""تذكير المغادرة — رسالة تصل قبل الخروج بساعات.

النزيل ينسى ساعة المغادرة، فيتأخّر ويُفاجأ برسم يوم إضافي، أو يخرج مبكرًا
بلا داعٍ. والاستقبال يقضي صباحه يتّصل غرفةً غرفة. رسالة واحدة تسبق الموعد
تحلّ الاثنين، وتفتح باب التمديد قبل أن يفوت وقته.

ثلاث قواعد تحكم الإرسال:

**مرّة واحدة.** المجدول يمرّ كل بضع دقائق، وعلامة الإرسال هي وحدها ما يمنع
التذكير أن يصير سيلًا. تُكتب مع الرسالة في معاملة واحدة، فانقطاعٌ في
المنتصف لا ينتج رسالةً بلا علامة.

**لا في ساعات النوم.** نزيل في مكة ينام بعد الفجر أو بين العشاء والتهجّد.
تنبيهٌ صوتي الثالثة فجرًا أسوأ من لا تذكير أصلًا — فيُؤجَّل إلى الصباح. وإن
كانت المغادرة نفسها في ساعة نوم، أُرسل مساء أمسها لا فجرها.

**لا على إقامة منتهية.** من غادر لا يُذكَّر بالمغادرة.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import RIYADH, now_riyadh
from .models import Guest, Message, Stay, Tenant
from .stay import CHECKOUT_GRACE, _aware, is_active

logger = logging.getLogger(__name__)

# ساعات لا نوقظ فيها أحدًا. التذكير الذي يقع داخلها يُؤجَّل إلى بدايتها.
QUIET_START = time(22, 30)
QUIET_END = time(7, 30)

# أقصى تأخير مقبول. تذكيرٌ فات موعده بأكثر من هذا لم يعد تذكيرًا — إرساله
# بعد المغادرة إزعاج، فيُطوى بلا إرسال.
MAX_LATE = timedelta(hours=6)

TEXT = {
    "ar": "تذكير: موعد المغادرة الساعة {time} اليوم. لو تحتاج تمديدًا أو "
          "مساعدة في الأمتعة، اكتب لنا هنا ونرتّبها.",
    "en": "Reminder: check-out is at {time} today. Need a late check-out or "
          "help with luggage? Just message us here.",
    "ur": "یاد دہانی: آج چیک آؤٹ کا وقت {time} ہے۔ توسیع یا سامان میں مدد "
          "چاہیے؟ یہیں پیغام بھیجیں۔",
    "id": "Pengingat: check-out hari ini pukul {time}. Butuh perpanjangan "
          "atau bantuan bagasi? Kirim pesan di sini.",
    "tr": "Hatırlatma: çıkış saati bugün {time}. Geç çıkış veya bagaj "
          "yardımı için bize buradan yazın.",
    "bn": "স্মরণ করিয়ে দিচ্ছি: আজ চেক-আউট {time} টায়। দেরিতে চেক-আউট বা "
          "লাগেজে সাহায্য লাগলে এখানে লিখুন।",
}


@dataclass(frozen=True)
class Sent:
    stay_id: int
    room: str
    text: str


def _in_quiet_hours(moment: datetime) -> bool:
    """النافذة تعبر منتصف الليل، فالمقارنة ليست «بين» عاديةً."""
    clock = moment.timetz().replace(tzinfo=None)
    return clock >= QUIET_START or clock < QUIET_END


def _shift_out_of_quiet(moment: datetime) -> datetime:
    """يزحزح الموعد إلى أول لحظة مسموحة بعده."""
    if not _in_quiet_hours(moment):
        return moment
    day = moment.date() if moment.timetz().replace(tzinfo=None) < QUIET_END \
        else moment.date() + timedelta(days=1)
    return datetime.combine(day, QUIET_END, tzinfo=RIYADH)


def checkout_at(stay: Stay) -> datetime:
    """ساعة المغادرة المعلنة — أي الانتهاء ناقصَ مهلة السماح."""
    return _aware(stay.expires_at) - CHECKOUT_GRACE


def _last_moment_before_quiet(before: datetime) -> datetime:
    """آخر لحظة مسموحة تسبق هذا الوقت."""
    # دقيقة قبل بدايتها: بدايةُ الهدوء نفسها داخله، فإرجاعها يُبقي العطل.
    evening = datetime.combine(before.date(), QUIET_START, tzinfo=RIYADH) - timedelta(minutes=1)
    while evening >= before:
        evening -= timedelta(days=1)
    return evening


def due_at(stay: Stay, hours_before: int) -> Optional[datetime]:
    """متى يُرسل تذكير هذه الإقامة، أو None إن كان التذكير مطفأً.

    الوقوع في ساعات النوم يزحزح الموعد — لكن إلى أيّ جهة يعتمد على المغادرة
    نفسها. تأجيلٌ إلى الصباح يقع بعد مغادرةٍ فجرية، فيصير تذكيرًا بشيء انتهى.
    فحين لا يسع التأجيل، نُقدّم بدل أن نُؤجّل: من يغادر السادسة فجرًا يُخبَر
    ليلة أمسها، لا بعد أن يكون قد خرج.
    """
    if hours_before <= 0:
        return None
    checkout = checkout_at(stay)
    wanted = checkout - timedelta(hours=hours_before)
    if not _in_quiet_hours(wanted):
        return wanted

    morning = _shift_out_of_quiet(wanted)
    if morning < checkout:
        return morning
    return _last_moment_before_quiet(wanted)


def pending(db: Session, tenant: Tenant, at: Optional[datetime] = None) -> list[Stay]:
    """الإقامات التي حلّ موعد تذكيرها ولم تُذكَّر بعد."""
    hours = int(getattr(tenant, "checkout_reminder_hours", 0) or 0)
    if hours <= 0:
        return []
    at = _aware(at or now_riyadh())

    # حتى اللحاق المتأخّر لا يخترق ساعات النوم. موعدٌ فات والمجدول نائم ثم
    # استيقظ الثالثة فجرًا كان يرنّ حينها — وهو بالضبط ما تمنعه هذي القاعدة.
    if _in_quiet_hours(at):
        return []

    rows = db.scalars(
        select(Stay).where(Stay.tenant_id == tenant.id, Stay.status == "open",
                           Stay.reminder_sent_at.is_(None))
    ).all()

    out = []
    for stay in rows:
        if not is_active(stay, at):
            continue
        when = due_at(stay, hours)
        # لا قبل موعده، ولا بعد فواته بكثير: تذكير بالمغادرة يصل بعدها إزعاج.
        if when is not None and when <= at <= when + MAX_LATE:
            out.append(stay)
    return out


def _thread(db: Session, tenant_id: int, stay: Stay) -> Guest:
    """محادثة هذه الإقامة بعينها — لا محادثة الغرفة.

    مشتقّ من الإقامة كما في صفحة النزيل: تذكيرٌ يهبط في محادثة من غادر
    يقرؤه النزيل الجديد في نفس الغرفة.
    """
    wa_id = f"web:{stay.id}"
    guest = db.scalar(select(Guest).where(Guest.tenant_id == tenant_id,
                                          Guest.wa_id == wa_id))
    if guest is None:
        guest = Guest(tenant_id=tenant_id, wa_id=wa_id, room=stay.room,
                      name=stay.guest_name or "", language="ar")
        db.add(guest)
        db.flush()
    return guest


def text_for(stay: Stay, language: str) -> str:
    when = checkout_at(stay).strftime("%H:%M")
    return TEXT.get(language or "ar", TEXT["ar"]).format(time=when)


def send_due(db: Session, tenant: Tenant, at: Optional[datetime] = None) -> list[Sent]:
    """يرسل ما حلّ موعده، ويعلّم كلّ إقامة في نفس المعاملة.

    الرسالة تهبط في محادثة النزيل، فيراها فورًا إن كانت الصفحة مفتوحة —
    الاستطلاع والنغمة مبنيان أصلًا — ويجدها بانتظاره إن فتحها لاحقًا.
    """
    at = _aware(at or now_riyadh())
    sent: list[Sent] = []
    for stay in pending(db, tenant, at):
        guest = _thread(db, tenant.id, stay)
        body = text_for(stay, guest.language or "ar")
        db.add(Message(
            tenant_id=tenant.id, guest_id=guest.id, direction="out", text=body,
            language=guest.language or "ar", intent="checkout_reminder",
            sent_by="", created_at=at,
        ))
        stay.reminder_sent_at = at
        sent.append(Sent(stay_id=stay.id, room=stay.room, text=body))
    if sent:
        db.flush()
        logger.info("أُرسل تذكير المغادرة لـ%d إقامة في %s", len(sent), tenant.slug)
    return sent
