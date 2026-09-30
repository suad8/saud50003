"""العمل الدوري — ما يجب أن يجري بلا أن يطلبه أحد.

كان في النظام عملٌ مجدول لا يجري: `expire_due` مكتوبة ومختبَرة ولا يناديها
شيء في الإنتاج. لم يظهر أثرها لأن كل فحص وصول يتحقّق من الانتهاء بنفسه —
فالأمان سليم — لكن صفوف الإقامات المنتهية تبقى مفتوحة إلى الأبد، وأجهزتها
معها. الآن مجدولٌ واحد يقوم بالاثنين: يطوي ما انتهى، ويذكّر من قارب.

ولماذا داخل العملية لا مهمة خارجية: النظام يجب أن يعمل بنشرٍ واحد بلا
إعداد. ومن أراد فصله شغّل `python -m daif.cli tick` من مجدول المنصّة
وأطفأ الداخلي بـ DAIF_TICK=off.
"""

from __future__ import annotations

import logging
import os
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from . import reminders
from .clock import now_riyadh
from .models import Tenant
from .stay import expire_due

logger = logging.getLogger(__name__)

# كل خمس دقائق. التذكير لا يحتاج دقّة الثانية، والمرور الرخيص أفضل من
# مرورٍ ثقيل نادر: دفعة كبيرة بعد انقطاع طويل تصل كلها متأخّرة.
INTERVAL = 300


@dataclass
class Report:
    expired: int = 0
    reminded: int = 0
    tenants: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        line = (f"فنادق {self.tenants} · إقامات طُويت {self.expired} · "
                f"تذكيرات {self.reminded}")
        return line + (f" · أعطال {len(self.errors)}" if self.errors else "")


def run_once(db, at: Optional[datetime] = None) -> Report:
    """مرور واحد على كل الفنادق. عطلٌ في فندق لا يوقف البقية."""
    at = at or now_riyadh()
    report = Report()
    for tenant in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
        report.tenants += 1
        try:
            report.expired += expire_due(db, tenant.id, at=at)
            report.reminded += len(reminders.send_due(db, tenant, at=at))
            db.commit()
        except Exception as exc:  # فندق واحد لا يُسقط المجدول كله
            db.rollback()
            report.errors.append(f"{tenant.slug}: {exc}")
            logger.exception("تعثّر العمل الدوري في %s", tenant.slug)
    return report


def enabled() -> bool:
    return os.environ.get("DAIF_TICK", "").strip().lower() not in {"off", "0", "false"}


def start_background(session_factory, interval: int = INTERVAL) -> threading.Thread:
    """خيط خلفي يمرّ كل فترة. خفيّ، ولا يمنع إغلاق العملية."""

    def loop() -> None:
        # لا نمرّ فور الإقلاع: نشرٌ جديد قد يرفع عدّة نسخ في نفس اللحظة،
        # فيتزاحمن على نفس الصفوف. والتأخير يفرّقهن.
        _time.sleep(min(interval, 30))
        while True:
            try:
                with session_factory() as db:
                    report = run_once(db)
                if report.reminded or report.expired:
                    logger.info("العمل الدوري: %s", report)
            except Exception:
                logger.exception("تعثّر المجدول — يواصل")
            _time.sleep(interval)

    thread = threading.Thread(target=loop, name="daif-tick", daemon=True)
    thread.start()
    return thread
