# واجهة الربط مع أنظمة إدارة الفنادق

دليل للمطوّر اللي بيربط نظام إدارة الفندق (جرس، فندقة، Hotelogix، أو نظام
داخلي) مع ضيف.

---

## ليش الربط أصلًا

المساعد **ما يفتح تذكرة إلا لغرفة موثّقة**. بدون ربط، موظف الاستقبال يدخل
رقم واتساب كل نزيل ويربطه بغرفته يدويًا — وهذا أكبر احتكاك في التشغيل،
وأكثر سبب تموت فيه التجارب.

مع الربط: الغرفة تجي مع تسجيل الوصول، وتنفكّ مع المغادرة. صفر لمسة بشرية.

---

## المصادقة

مفتاح لكل فندق، من **الإعدادات ← مفاتيح الربط** (المالك بس يقدر).

```
Authorization: Bearer daif_<بادئة>_<سرّ>
```

- المفتاح يُعرض **مرة وحدة** عند الإنشاء. ضاع = أنشئ غيره وألغِ القديم.
- الفندق يُشتقّ من المفتاح **دائمًا**. لا ترسل معرّف فندق في متن الطلب —
  بيُرفض الطلب كامل.
- الحد: ٣٠٠ طلب في الدقيقة لكل عنوان.

فحص سريع:

```bash
curl -H "Authorization: Bearer $DAIF_KEY" https://<نطاقك>/api/v1/ping
# {"ok":true,"hotel":"فندق طيبة","slug":"taibah"}
```

---

## المسارات

### تسجيل وصول — يربط الرقم بالغرفة

```http
POST /api/v1/guests/check-in
```

```json
{
  "wa_id": "966500000001",
  "room": "402",
  "name": "أحمد",
  "language": "ur",
  "checkout_on": "2026-09-10"
}
```

الرقم يُنظَّف تلقائيًا: `+966 50-000-0001` تصير `966500000001`.

**مطوّف يدير مجموعة:**

```json
{
  "wa_id": "966500000002",
  "room": "301",
  "name": "المطوّف سعود",
  "group_mode": "group_leader",
  "group_rooms": ["501", "502", "503"]
}
```

بعدها طلب واحد منه يفتح تذكرة لكل غرفة في مجموعته — **ولا يتجاوزها**.
غرفة برّا القائمة تُرفض حتى لو أصرّ.

### مغادرة — تفكّ الغرفة

```http
POST /api/v1/guests/check-out
{"wa_id": "966500000001"}
```

بعدها المساعد ما يفتح ولا تذكرة لهالرقم. **ناد هذا المسار وقت تسليم
الغرفة لا آخر اليوم** — غرفة سلّمها صاحبها وانفتحت لها تذكرة يعني عامل
تدبير راح لغرفة فيها نزيل ثاني.

### سحب التذاكر

```http
GET /api/v1/tickets?status=open&limit=100
```

`status`: `open` | `in_progress` | `done` | `cancelled` | `all`

```json
{"count": 1, "tickets": [{
  "id": 41, "type": "towels", "room": "402",
  "detail": "طلب مناشف إضافية", "requested_time": null,
  "urgency": "normal", "status": "open", "escalated": false,
  "created_at": "2026-09-01T14:22:11+03:00"
}]}
```

`detail` **بالعربي دائمًا** مهما كانت لغة النزيل — عشان عامل التدبير يقرأها
بلا مترجم.

### إغلاق تذكرة

```http
POST /api/v1/tickets/41/status
{"status": "done"}
```

عشان العامل ما يحتاج لوحتين.

---

## الأكواد

| الكود | معناه |
|---|---|
| `200` | تم |
| `401` | مفتاح ناقص أو غلط أو ملغى |
| `404` | غير موجود — **أو يخصّ فندقًا ثانيًا** |
| `422` | مدخلات غير صالحة |
| `429` | تجاوزت الحد |

> `404` مقصودة لسجل فندق آخر: ما نقول «موجود بس ممنوع» — هذا يكشف وجوده.

---

## نمط التكامل الموصى به

```
تسجيل وصول في الـPMS ──► POST /guests/check-in
تسليم الغرفة        ──► POST /guests/check-out
شاشة التدبير        ──► GET  /tickets?status=open   (كل ٦٠ ثانية)
إغلاق المهمة        ──► POST /tickets/{id}/status
```

**نقطة مهمة:** رقم واتساب اللي ترسله لازم يكون الرقم اللي بيراسل منه النزيل
فعلًا — مو رقم الحجز ولا رقم الحملة. لو الحجز باسم منظّم الرحلة، أرسل رقم
النزيل نفسه، وإلا التذاكر بتُفتح باسم غرفة غلط.

---

## مثال كامل

```python
import httpx

BASE = "https://<نطاقك>/api/v1"
HEAD = {"Authorization": f"Bearer {DAIF_KEY}"}

def on_check_in(booking):
    httpx.post(f"{BASE}/guests/check-in", headers=HEAD, json={
        "wa_id": booking.guest_mobile,
        "room": booking.room_number,
        "name": booking.guest_name,
        "checkout_on": booking.departure.isoformat(),
    }).raise_for_status()

def on_check_out(booking):
    httpx.post(f"{BASE}/guests/check-out", headers=HEAD,
               json={"wa_id": booking.guest_mobile}).raise_for_status()

def pull_open_tickets():
    r = httpx.get(f"{BASE}/tickets", headers=HEAD, params={"status": "open"})
    r.raise_for_status()
    return r.json()["tickets"]
```
