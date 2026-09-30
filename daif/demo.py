"""وضع العرض: بيانات دخول معلَنة على الصفحة نفسها.

نشر تجريبي يُراد أن يجرّبه الناس بلا تنسيق مسبق. فإخفاء كلمة المرور فيه لا
يحمي شيئًا — كل من يُفترض أن يدخل يعرفها — ويكلّف كل زائر رسالةً يسأل بها.

فالوضع يُعلن نفسه بدل أن يتظاهر: شريط ظاهر فوق نموذج الدخول يحمل البيانات،
ويقول صراحةً إن المنصة تجريبية وإن ما يُكتب فيها يراه غيرك.

وهو حالة مخزَّنة لا متغيّر بيئة: يُفعَّل بأمر `demo`، ويُطفأ بضغطة من اللوحة
فور أن يصير النشر حقيقيًا — بلا إعادة نشر.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ROW = "demo_banner"


def enable(session, *, platform_email: str, staff_email: str,
           password: str, hotel: str) -> None:
    from .models import PlatformSecret

    body = json.dumps({"platform_email": platform_email, "staff_email": staff_email,
                       "password": password, "hotel": hotel}, ensure_ascii=False)
    row = session.get(PlatformSecret, ROW)
    if row is None:
        session.add(PlatformSecret(name=ROW, value=body))
    else:
        row.value = body
    session.flush()


def disable(session) -> bool:
    """يطفئ الوضع. يعيد True إن كان مفعّلًا."""
    from .models import PlatformSecret

    row = session.get(PlatformSecret, ROW)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def banner(session) -> dict | None:
    """بيانات الشريط، أو لا شيء حين يكون الوضع مطفأً."""
    from .models import PlatformSecret

    try:
        row = session.get(PlatformSecret, ROW)
    except Exception:                      # noqa: BLE001
        # صفحة الدخول يجب أن تُعرض حتى لو تعذّرت القراءة — الشريط زينة لا شرط.
        return None
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except (ValueError, TypeError):
        logger.warning("محتوى شريط العرض غير صالح — تُجوهل.")
        return None


# --- زرع منصة العرض ---

PASSWORD = "Daif-Demo-2026"
DEFAULT_HOTEL = "فندق طيبة"
DEFAULT_SLUG = "taibah"
DEFAULT_EMAIL = "admin@daif.sa"


class Seeded:
    """ما أُنشئ، ليطبعه سطر الأوامر أو يسجّله الخادم."""

    def __init__(self, platform_email: str, staff_email: str,
                 password: str, codes: list[tuple[str, str]]) -> None:
        self.platform_email = platform_email
        self.staff_email = staff_email
        self.password = password
        self.codes = codes


def seed(session, *, hotel: str = DEFAULT_HOTEL, slug: str = DEFAULT_SLUG,
         platform_email: str = DEFAULT_EMAIL,
         password: str = PASSWORD) -> Seeded | None:
    """يملأ منصة فارغة بفندق عرض كامل. يرجع None إن لم تكن فارغة.

    مشترك بين أمر `demo` وأول إقلاع، عمدًا: نسختان من هذا الزرع كانتا
    ستنحرفان، فيعرض الموقع بيانات دخول لا تطابق ما في قاعدة البيانات.
    """
    from datetime import timedelta

    import yaml
    from sqlalchemy import select

    from . import stay as stay_mod
    from .clock import now_riyadh
    from .models import (Fact, Guest, HandoffRecord, Message, PlatformAdmin,
                     StaffUser, Tenant, Ticket)
    from .security import hash_password

    if session.scalar(select(Tenant).limit(1)) is not None:
        return None

    if session.scalar(select(PlatformAdmin).limit(1)) is None:
        session.add(PlatformAdmin(email=platform_email, name="مشغّل المنصة",
                                  password_hash=hash_password(password)))

    tenant = Tenant(slug=slug, name=hotel, plan="pro",
                    city="المدينة المنورة", access_mode="stay_code")
    session.add(tenant)
    session.flush()

    staff_email = f"reem@{slug}.sa"
    session.add(StaffUser(tenant_id=tenant.id, email=staff_email,
                          name="ريم · الاستقبال", role="owner",
                          password_hash=hash_password(password)))

    # قاعدة المعرفة مفعّلة هنا خلافًا للفندق الحقيقي: العرض بلا حقائق ظاهرة
    # يحوّل كل سؤال لموظف، فلا يُرى منه شيء.
    records = yaml.safe_load((ROOT / "data" / "knowledge_base.yaml").read_text("utf-8"))
    for rec in records:
        session.add(Fact(
            tenant_id=tenant.id, key=rec["id"], text=rec["text"],
            topic=rec.get("topic", ""), active=True,
            seasons=",".join(rec.get("seasons", ["normal", "ramadan", "hajj"])),
            hours=rec.get("hours", "") or "",
            paid=bool(rec.get("paid", False)),
        ))

    today = now_riyadh().date()
    # أرقام يسهل كتابتها في العرض: الجوال هو مفتاح الدخول الآن، فالمجرِّب
    # يحتاج رقمًا يتذكّره لا رمزًا يبحث عنه.
    codes: list[tuple[str, str]] = []
    for room, name, phone in [("402", "أحمد الغامدي", "0500000001"),
                              ("318", "محمد أسلم", "0500000002"),
                              ("215", "Siti Rahayu", "0500000003")]:
        stay_mod.open_stay(session, tenant.id, room, guest_name=name,
                           phone=phone, checkout_on=today + timedelta(days=3))
        codes.append((room, phone))

    enable(session, platform_email=platform_email, staff_email=staff_email,
           password=password, hotel=hotel)

    # التذاكر تُربط بنزلاء لهم محادثات حقيقية، لا تُنثر بلا صاحب. تذكرةٌ بلا
    # نزيل تُخفي زرّ «فتح المحادثة» — فيبدو أهمّ ما في الشاشة معطوبًا في
    # العرض نفسه الذي يُفترض أن يبيّنه.
    now = now_riyadh()
    threads = [
        ("318", "صيانة", "المكيف ما يبرّد — مكرّرة من أمس", "urgent", "open",
         [("in", "المكيف ما يبرّد من أمس، والغرفة حارة"),
          ("out", "آسفين على الإزعاج. رفعت طلب صيانة عاجل وبيوصلك فني خلال ٢٠ دقيقة.")]),
        ("512", "تدبير فندقي", "مناشف إضافية ووسادة", "normal", "open",
         [("in", "ممكن مناشف زيادة ووسادة؟"),
          ("out", "أبشر. وصل طلبك لتدبير الغرف.")]),
        ("407", "تدبير فندقي", "تنظيف الغرفة الساعة ٤ العصر", "normal", "in_progress",
         [("in", "أبغى تنظيف الغرفة الساعة ٤ العصر")]),
        ("233", "صيانة", "الدش ماؤه بارد", "normal", "done",
         [("in", "الدش ماؤه بارد"),
          ("out", "تم الإصلاح، جرّبه الحين ولو بقي شي كلّمنا.")]),
    ]
    for index, (room, ty, detail, urg, status, script) in enumerate(threads):
        guest = Guest(tenant_id=tenant.id, wa_id=f"demo:{room}", room=room,
                      language="ar")
        session.add(guest)
        session.flush()
        last = None
        for step, (direction, text) in enumerate(script):
            last = Message(
                tenant_id=tenant.id, guest_id=guest.id, direction=direction,
                text=text, language="ar",
                sent_by=("ريم · الاستقبال" if direction == "out" else ""),
                created_at=now - timedelta(minutes=(len(threads) - index) * 12 - step),
            )
            session.add(last)
        session.flush()
        session.add(Ticket(tenant_id=tenant.id, guest_id=guest.id,
                           message_id=last.id if last else None,
                           type=ty, room=room, detail=detail, urgency=urg,
                           status=status, created_at=now))

    # تحويل واحد مفتوح: شكوى لا يجوز للمساعد أن يجيب عنها، فتنتظر إنسانًا.
    complainant = Guest(tenant_id=tenant.id, wa_id="demo:608", room="608",
                        language="ar")
    session.add(complainant)
    session.flush()
    grievance = "الغرفة اللي وصلتني مو اللي حجزتها، وأبغى أكلم المسؤول"
    session.add(Message(tenant_id=tenant.id, guest_id=complainant.id,
                        direction="in", text=grievance, language="ar",
                        created_at=now - timedelta(minutes=6)))
    session.flush()
    session.add(HandoffRecord(
        tenant_id=tenant.id, guest_id=complainant.id, reason="complaint",
        to="desk", guest_text=grievance, status="open",
        note="شكوى — المساعد لا يجيب عنها ويحوّلها فورًا",
        created_at=now - timedelta(minutes=6)))

    # فجوات معرفة: أسئلة سألها نزلاء ولم تجد حقيقة تغطيها. شاشة «الفجوات»
    # تعرضها مرتّبة بالتكرار، وهي أوضح ما يقنع صاحب الفندق: النظام يقول له
    # ما ينقص قاعدة معرفته بدل أن ينتظر منه أن يخمّنه. وكانت تُعرض فارغة.
    for question, times in [("فيه صيدلية قريبة من الفندق؟", 4),
                            ("عندكم كوي ملابس سريع؟", 3),
                            ("متى يفتح السوق اللي جنب الحرم؟", 2),
                            ("فيه كراسي أطفال في المطعم؟", 1)]:
        for turn in range(times):
            asker = Guest(tenant_id=tenant.id, wa_id=f"gap:{abs(hash(question)) % 9999}:{turn}",
                          room="", language="ar")
            session.add(asker)
            session.flush()
            session.add(HandoffRecord(
                tenant_id=tenant.id, guest_id=asker.id,
                reason="no_documented_answer", to="desk", guest_text=question,
                note="لا حقيقة موثّقة تغطي السؤال", status="resolved",
                resolved_by=staff_email,
                created_at=now - timedelta(hours=turn * 5 + 2)))

    return Seeded(platform_email, staff_email, password, codes)
