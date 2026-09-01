"""تحديد معدّل الطلبات — نافذة منزلقة في الذاكرة.

يكفي لخادم واحد. عند التوسع لأكثر من عملية، يُستبدل المخزن بـ Redis دون
تغيير الواجهة: `hit()` هي كل ما يستعمله الاستدعاء.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Limit:
    """عدد المحاولات المسموح بها خلال نافذة زمنية بالثواني."""

    attempts: int
    window: float


# محاولات الدخول: الحماية من التخمين المتكرر لكلمة المرور.
LOGIN = Limit(attempts=8, window=15 * 60)
# الـwebhook: سخيّ لأنه مسار مشروع، لكنه ليس مفتوحًا بلا حد.
WEBHOOK = Limit(attempts=600, window=60)


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: Limit, *, now: float | None = None) -> bool:
        """يسجّل محاولة. يعيد True إن كانت مسموحة، وFalse إن تجاوزت الحد."""
        moment = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = moment - limit.window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit.attempts:
                return False
            bucket.append(moment)
            return True

    def remaining(self, key: str, limit: Limit, *, now: float | None = None) -> int:
        moment = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._hits.get(key, deque())
            cutoff = moment - limit.window
            live = sum(1 for stamp in bucket if stamp > cutoff)
            return max(0, limit.attempts - live)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


limiter = RateLimiter()
