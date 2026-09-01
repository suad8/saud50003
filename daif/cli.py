"""أداة سطر أوامر: تهيئة قاعدة البيانات، وإنشاء فندق، واستيراد قاعدة معرفة."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import yaml

from .clock import parse_date
from .db import init_db, session_scope
from .billing import format_sar  # noqa: F401
from .knowledge import KnowledgeBase
from .models import Fact, StaffUser, Tenant
from .repository import staff_by_email, tenant_by_slug
from .security import hash_password


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

    serve = sub.add_parser("serve", help="تشغيل الخادم")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
