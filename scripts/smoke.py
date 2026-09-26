#!/usr/bin/env python3
"""يفحص نشرًا حيًّا من الخارج: هل قام فعلًا، وهل حواجزه قائمة؟

    python scripts/smoke.py https://your-app.up.railway.app

«يعمل» ليست حالة واحدة. خادم يردّ ٢٠٠ على الصفحة الرئيسية قد يكون بلا قاعدة
بيانات، أو بلوحة مفتوحة بلا تسجيل دخول، أو بمسار نزيل يقبل كودًا ملفّقًا.
فنفحص كل واحدة منها صراحةً.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 20


def get(url: str, redirect: bool = True):
    """يعيد (الحالة، النص). لا يتبع التحويل حين نريد قياسه."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(
        *( [] if redirect else [NoRedirect] )
    )
    try:
        with opener.open(urllib.request.Request(
                url, headers={"User-Agent": "daif-smoke"}), timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                       # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="فحص نشر حيّ")
    ap.add_argument("base", help="عنوان النشر، مثل https://x.up.railway.app")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    checks: list[tuple[str, bool, str]] = []

    def ok(name, cond, note=""):
        checks.append((name, bool(cond), note))

    if not base.startswith("https://"):
        ok("HTTPS", False, "العنوان ليس https — الكوكيات الآمنة لن تُرسل")
    else:
        ok("HTTPS", True)

    code, body = get(f"{base}/healthz")
    ok("فحص الصحة", code == 200, f"HTTP {code}")

    code, body = get(f"{base}/")
    ok("الصفحة العامة", code == 200, f"HTTP {code}")

    code, body = get(f"{base}/login")
    ok("صفحة الدخول", code == 200 and "csrf" in body.lower(), f"HTTP {code}")

    # اللوحة يجب ألا تُفتح بلا جلسة
    code, _ = get(f"{base}/stays", redirect=False)
    ok("اللوحة محمية", code in (302, 303, 307), f"HTTP {code} — يُتوقع تحويل للدخول")

    code, _ = get(f"{base}/platform", redirect=False)
    ok("لوحة المنصة محمية", code in (302, 303, 307), f"HTTP {code}")

    # كود غرفة ملفّق يجب أن يُرفض
    code, body = get(f"{base}/g/anyhotel/402/zzzzzzzzzz")
    ok("كود الغرفة الملفّق يُرفض", code == 200 and "مو صحيح" in body,
       f"HTTP {code} — يجب أن تظهر رسالة رفض")

    # الواجهة البرمجية تطلب مفتاحًا
    code, _ = get(f"{base}/api/v1/ping")
    ok("الواجهة البرمجية تطلب مفتاحًا", code in (401, 403), f"HTTP {code}")

    # ترويسات الأمان
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"{base}/login", headers={"User-Agent": "daif-smoke"}), timeout=TIMEOUT) as r:
            h = {k.lower(): v for k, v in r.headers.items()}
        ok("ترويسات الأمان", "content-security-policy" in h and
           h.get("x-content-type-options") == "nosniff",
           "CSP أو nosniff ناقصة")
        ok("HSTS", "strict-transport-security" in h, "غير مضبوطة")
    except Exception as e:                       # noqa: BLE001
        ok("ترويسات الأمان", False, str(e))

    # لا نحاذي بالمسافات: عرض الحرف العربي في الطرفية غير ثابت، فالمحاذاة
    # الحسابية تنتج أعمدة متعرّجة. السطر المستقل أوضح.
    failed = 0
    print()
    for name, good, note in checks:
        print(f"{'✅' if good else '❌'}  {name}")
        if not good and note:
            print(f"      {note}")
        failed += 0 if good else 1
    print()
    local = base.startswith("http://")
    if failed and local:
        print("ملاحظة: تفحص عنوانًا بلا https، فـHTTPS وHSTS تفشلان دائمًا هنا.")
        print("هذا متوقّع محليًا. المهم أن يبقى ما عداهما أخضر.")
    elif failed:
        print(f"{failed} فحص فشل. راجع سجل النشر ودليل docs/DEPLOY.md.")
    else:
        print("النشر سليم. الخطوة التالية: ادخل /platform وأنشئ أول فندق.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
