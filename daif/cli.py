"""أداة سطر أوامر: تهيئة قاعدة البيانات، وإنشاء فندق، واستيراد قاعدة معرفة."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import yaml
from sqlalchemy import select

from .clock import parse_date
from .config import server_port
from .db import init_db, session_scope
from .billing import format_sar  # noqa: F401
from .knowledge import KnowledgeBase
from .models import Fact, StaffUser, Tenant
from .repository import staff_by_email, tenant_by_slug
from .security import hash_password, verify_password

ROOT = Path(__file__).resolve().parent.parent


def cmd_init(args: argparse.Namespace) -> int:
    init_db()
    print("تم إنشاء الجداول.")
    return 0


def cmd_create_hotel(args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        if tenant_by_slug(session, args.slug):
            print(f"الفندق «{args.slug}» موجود مسبقًا.", file=sys.stderr)
            return 1
        tenant = Tenant(
            slug=args.slug,
            name=args.name,
            city=args.city,
            hk_window=args.hk_window,
            wa_phone_number_id=args.phone_number_id or "",
        )
        session.add(tenant)
        session.flush()
        print(f"أُنشئ الفندق «{tenant.name}» برقم {tenant.id}.")
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    init_db()
    password = args.password or getpass.getpass("كلمة المرور: ")
    if len(password) < 8:
        print("كلمة المرور قصيرة جدًا (٨ محارف على الأقل).", file=sys.stderr)
        return 1
    with session_scope() as session:
        tenant = tenant_by_slug(session, args.hotel)
        if tenant is None:
            print(f"لا يوجد فندق باسم «{args.hotel}».", file=sys.stderr)
            return 1
        if staff_by_email(session, args.email):
            print("البريد مستخدم مسبقًا.", file=sys.stderr)
            return 1
        session.add(
            StaffUser(
                tenant_id=tenant.id,
                email=args.email.strip().lower(),
                name=args.name or "",
                password_hash=hash_password(password),
                role=args.role,
                locale=args.locale,
            )
        )
        print(f"أُنشئ المستخدم {args.email} في «{tenant.name}».")
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    """حساب مشغّل المنصة — منفصل تمامًا عن موظفي الفنادق."""
    from .models import PlatformAdmin
    from .repository import platform_admin_by_email

    init_db()
    password = args.password or getpass.getpass("كلمة المرور: ")
    if len(password) < 12:
        print("كلمة مرور مشغّل المنصة يجب ألا تقل عن ١٢ محرفًا.", file=sys.stderr)
        return 1
    with session_scope() as session:
        if platform_admin_by_email(session, args.email):
            print("البريد مستخدم مسبقًا.", file=sys.stderr)
            return 1
        session.add(
            PlatformAdmin(
                email=args.email.strip().lower(),
                name=args.name or "",
                password_hash=hash_password(password),
            )
        )
        print(f"أُنشئ مشغّل المنصة {args.email}.")
    return 0


def cmd_invoices(args: argparse.Namespace) -> int:
    """إصدار فواتير فترة محددة لكل الفنادق النشطة."""
    from . import billing
    from .repository import list_tenants

    init_db()
    period = args.period or billing.previous_period(billing.period_of())
    with session_scope() as session:
        issued = 0
        for tenant in list_tenants(session, include_inactive=False):
            invoice = billing.issue_invoice(session, tenant, period)
            issued += 1
            print(f"  {tenant.slug:16} {invoice.number}  {billing.format_sar(invoice.total)} ر.س")
        print(f"أُصدرت {issued} فاتورة لفترة {period}.")
    return 0


def cmd_import_kb(args: argparse.Namespace) -> int:
    init_db()
    path = Path(args.file)
    records = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    # التحقق قبل الكتابة: قاعدة فاسدة تُرفض كاملة لا جزئيًا.
    KnowledgeBase.from_records(records)

    with session_scope() as session:
        tenant = tenant_by_slug(session, args.hotel)
        if tenant is None:
            print(f"لا يوجد فندق باسم «{args.hotel}».", file=sys.stderr)
            return 1
        existing = {f.key: f for f in session.query(Fact).filter(Fact.tenant_id == tenant.id)}
        added = updated = 0
        for record in records:
            seasons = record.get("season") or ["normal", "ramadan", "hajj"]
            if isinstance(seasons, str):
                seasons = [seasons]
            fields = dict(
                text=str(record["text"]).strip(),
                topic=str(record.get("topic") or ""),
                seasons=",".join(seasons),
                hours=record.get("hours"),
                valid_from=parse_date(str(record["valid_from"])) if record.get("valid_from") else None,
                valid_until=parse_date(str(record["valid_until"])) if record.get("valid_until") else None,
                paid=bool(record.get("paid", False)),
                active=True,
                updated_by="cli",
            )
            key = str(record["id"])
            if key in existing:
                for name, value in fields.items():
                    setattr(existing[key], name, value)
                updated += 1
            else:
                session.add(Fact(tenant_id=tenant.id, key=key, **fields))
                added += 1
        print(f"استُوردت {added} حقيقة جديدة، وحُدّثت {updated}.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("daif.web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


# كلمات مرور العرض. ظاهرة عمدًا في الكود وفي الشاشة: هي مؤقتة ومعروفة
# ومعلَنة، لا سرّ يُظنّ أنه محفوظ. والأمر يرفض العمل على قاعدة فيها بيانات،
# فلا يمكن أن يفتح بابًا في منصة قائمة.
DEMO_PW = "Daif-Demo-2026"


def cmd_demo(args: argparse.Namespace) -> int:
    """يجهّز منصة كاملة جاهزة للعرض بأمر واحد.

    المشكلة التي يحلّها: بعد أول نشر لا يوجد شيء — لا مشغّل، ولا فندق، ولا
    غرفة، ولا حقيقة واحدة. فمن يريد أن يرى النظام يعمل يمرّ بست شاشات قبل
    أول جواب. هذا الأمر يقفز بها كلها.

    وهو مرفوض على قاعدة بيانات فيها بيانات: العرض يُجهَّز مرة على منصة
    فارغة، ولا يُقحَم على فندق يعمل.
    """
    from datetime import timedelta

    from . import stay as stay_mod
    from .clock import now_riyadh
    from .models import Fact, PlatformAdmin, StaffUser, Tenant, Ticket
    from .security import hash_password

    init_db()
    with session_scope() as session:
        if session.scalar(select(Tenant).limit(1)) is not None:
            print("‼ توجد فنادق في قاعدة البيانات. أمر العرض للمنصة الفارغة وحدها.")
            print("  أنشئ فندقًا من /platform، أو استعمل create-hotel.")
            return 1

        admin_email = args.email
        if session.scalar(select(PlatformAdmin).limit(1)) is None:
            session.add(PlatformAdmin(email=admin_email, name="مشغّل المنصة",
                                      password_hash=hash_password(DEMO_PW)))

        hotel = Tenant(slug=args.slug, name=args.hotel, plan="pro",
                       city="المدينة المنورة", access_mode="stay_code")
        session.add(hotel)
        session.flush()

        staff_email = f"reem@{args.slug}.sa"
        session.add(StaffUser(tenant_id=hotel.id, email=staff_email,
                              name="ريم · الاستقبال", role="owner",
                              password_hash=hash_password(DEMO_PW)))

        # قاعدة المعرفة مفعّلة هنا خلافًا للفندق الحقيقي: العرض بلا حقائق
        # ظاهرة يحوّل كل سؤال، فلا يُرى منه شيء.
        records = yaml.safe_load((ROOT / "data" / "knowledge_base.yaml").read_text("utf-8"))
        for rec in records:
            session.add(Fact(
                tenant_id=hotel.id, key=rec["id"], text=rec["text"],
                topic=rec.get("topic", ""), active=True,
                seasons=",".join(rec.get("seasons", ["normal", "ramadan", "hajj"])),
                hours=rec.get("hours", "") or "",
                paid=bool(rec.get("paid", False)),
            ))

        today = now_riyadh().date()
        codes = []
        for room, name, phone in [("402", "أحمد الغامدي", "966500000001"),
                                  ("318", "محمد أسلم", "923000000002"),
                                  ("215", "Siti Rahayu", "628100000003")]:
            st = stay_mod.open_stay(session, hotel.id, room, guest_name=name,
                                    phone=phone, checkout_on=today + timedelta(days=3))
            codes.append((room, st.stay_code))

        from . import demo as demo_mod
        demo_mod.enable(session, platform_email=admin_email, staff_email=staff_email,
                        password=DEMO_PW, hotel=args.hotel)

        now = now_riyadh()
        for ty, room, detail, urg, status in [
            ("صيانة", "318", "المكيف ما يبرّد — مكرّرة من أمس", "urgent", "open"),
            ("تدبير فندقي", "512", "مناشف إضافية ووسادة", "normal", "open"),
            ("تدبير فندقي", "407", "تنظيف الغرفة الساعة ٤ العصر", "normal", "in_progress"),
            ("صيانة", "233", "الدش ماؤه بارد", "normal", "done"),
        ]:
            session.add(Ticket(tenant_id=hotel.id, type=ty, room=room, detail=detail,
                               urgency=urg, status=status, created_at=now))

    base = args.url.rstrip("/") if args.url else "https://<نطاقك>"
    line = "─" * 54
    print()
    print(line)
    print("  جاهز. بيانات الدخول — مؤقتة ومعروفة، غيّرها قبل الاستعمال الحقيقي")
    print(line)
    print(f"  لوحة المنصة   {base}/platform/login")
    print(f"     البريد      {admin_email}")
    print(f"     كلمة المرور {DEMO_PW}")
    print()
    print(f"  لوحة الفندق   {base}/login")
    print(f"     البريد      {staff_email}")
    print(f"     كلمة المرور {DEMO_PW}")
    print(line)
    print("  إقامات مفتوحة، ورموزها للنزيل:")
    for room, code in codes:
        print(f"     غرفة {room}   الرمز {code}")
    print(line)
    print(f"  اطبع الملصقات  {base}/stays/stickers?rooms=401-410")
    print()
    print("  البيانات هذي تظهر أيضًا فوق نموذج الدخول، فالزائر يجرّب بلا ما يسألك.")
    print()
    print("  ⚠ قبل أول استعمال حقيقي:  python -m daif.cli demo-off")
    print("    يطفئ الشريط ويطلب منك كلمة مرور جديدة.")
    print()
    return 0


def cmd_demo_off(args: argparse.Namespace) -> int:
    """يطفئ وضع العرض ويجبر على تغيير كلمات المرور المعلَنة.

    الإطفاء وحده لا يكفي: كلمة المرور بقيت هي هي، ومعروفة، ومنشورة في هذا
    الملف. فمن يطفئ الشريط ويظن نفسه أغلق الباب يكون أسوأ حالًا ممن تركه
    ظاهرًا — يظن أنه محمي وليس كذلك.
    """
    from . import demo as demo_mod
    from .models import PlatformAdmin, StaffUser
    from .security import hash_password

    new_pw = args.password or getpass.getpass("كلمة مرور جديدة (١٢ محرفًا فأكثر): ")
    if len(new_pw) < 12:
        print("‼ كلمة المرور أقصر من ١٢ محرفًا.")
        return 1

    init_db()
    with session_scope() as session:
        was_on = demo_mod.disable(session)
        changed = 0
        for model in (PlatformAdmin, StaffUser):
            for row in session.scalars(select(model)).all():
                if verify_password(DEMO_PW, row.password_hash):
                    row.password_hash = hash_password(new_pw)
                    changed += 1

    print(f"أُطفئ الشريط." if was_on else "الشريط كان مطفأً أصلًا.")
    print(f"غُيّرت كلمة مرور {changed} حسابًا كانت على كلمة العرض.")
    if not changed:
        print("لا حساب على كلمة العرض — غيّرتها من قبل على ما يبدو.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daif", description="ضيف — أدوات التشغيل")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="إنشاء الجداول").set_defaults(func=cmd_init)

    hotel = sub.add_parser("create-hotel", help="إضافة فندق جديد")
    hotel.add_argument("slug")
    hotel.add_argument("name")
    hotel.add_argument("--city", default="المدينة المنورة")
    hotel.add_argument("--hk-window", default="08:00-16:00")
    hotel.add_argument("--phone-number-id", default="")
    hotel.set_defaults(func=cmd_create_hotel)

    user = sub.add_parser("create-user", help="إضافة موظف للوحة")
    user.add_argument("hotel")
    user.add_argument("email")
    user.add_argument("--name", default="")
    user.add_argument("--password", default="")
    user.add_argument("--role", default="owner", choices=["owner", "manager", "staff"])
    user.add_argument("--locale", default="ar")
    user.set_defaults(func=cmd_create_user)

    admin = sub.add_parser("create-admin", help="إضافة مشغّل للمنصة")
    admin.add_argument("email")
    admin.add_argument("--name", default="")
    admin.add_argument("--password", default="")
    admin.set_defaults(func=cmd_create_admin)

    inv = sub.add_parser("invoices", help="إصدار فواتير فترة")
    inv.add_argument("--period", default="", help="مثال 2026-09؛ الافتراضي الشهر المنقضي")
    inv.set_defaults(func=cmd_invoices)

    kb = sub.add_parser("import-kb", help="استيراد قاعدة معرفة من ملف YAML")
    kb.add_argument("hotel")
    kb.add_argument("file")
    kb.set_defaults(func=cmd_import_kb)

    demo = sub.add_parser("demo", help="تجهيز منصة عرض كاملة ببيانات دخول معلنة")
    demo.add_argument("--hotel", default="فندق طيبة")
    demo.add_argument("--slug", default="taibah")
    demo.add_argument("--email", default="admin@daif.sa")
    demo.add_argument("--url", default="", help="نطاق النشر لطباعة روابط جاهزة")
    demo.set_defaults(func=cmd_demo)

    off = sub.add_parser("demo-off", help="إطفاء وضع العرض وتغيير كلمات المرور المعلَنة")
    off.add_argument("--password", default="", help="كلمة المرور الجديدة (وإلا تُطلب تفاعليًا)")
    off.set_defaults(func=cmd_demo_off)

    serve = sub.add_parser("serve", help="تشغيل الخادم")
    serve.add_argument("--host", default="127.0.0.1")
    # الافتراضي من PORT: منصات النشر تحقنه ولا تقبل منفذًا ثابتًا
    serve.add_argument("--port", type=int, default=server_port())
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
