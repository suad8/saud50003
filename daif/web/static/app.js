// تفاعلات صغيرة لا تستحق إطار عمل.

// حقل غرف المجموعة يُفعَّل فور اختيار «مطوّف»، لا بعد الحفظ.
// الخادم يبقى المرجع: يفرّغ القائمة إن لم يكن الوضع مطوّفًا.
document.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement) || select.name !== "group_mode") return;
  const row = select.closest("tr");
  const rooms = row && row.querySelector('input[name="group_rooms"]');
  if (!rooms) return;
  const isLeader = select.value === "group_leader";
  rooms.disabled = !isLeader;
  if (!isLeader) rooms.value = "";
});

// شريط المنصة التجريبية: زر يملأ الحقول بدل نسخ البيانات يدويًا.
document.addEventListener("click", function (e) {
  var b = e.target.closest(".demo-fill");
  if (!b) return;
  var email = document.getElementById("email");
  var pw = document.getElementById("password");
  if (email) email.value = b.dataset.email || "";
  if (pw) pw.value = b.dataset.pw || "";
  if (pw) pw.focus();
});

// --- نبض الاستقبال: تذكرة جديدة تُسمَع، لا تنتظر تحديث الصفحة ---
//
// كانت اللوحة ساكنة تمامًا: نزيل يكتب سؤالًا فتُفتح تذكرة، ولا يعرف بها
// أحد حتى يحدّث الموظف الصفحة بنفسه. والنزيل ينتظر ردًّا لا يعلم أن أحدًا
// لم يره بعد. الاستقبال مشغول بالنزلاء أمامه، فالتنبيه يجب أن يصله هو.
(function () {
  "use strict";

  var nav = document.querySelector("nav");
  if (!nav || !document.querySelector('[data-nav="tickets"]')) return;

  var VISIBLE_MS = 15000;
  var HIDDEN_MS = 45000;
  var base = document.title;
  var known = null;
  var unseen = 0;
  var audio = null;
  var timer = null;

  function unlock() {
    if (audio) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try { audio = new Ctx(); } catch (e) { audio = null; }
  }

  // نغمة مولَّدة لا ملفًّا: لا طلب ثانٍ، ولا صمتٌ إن فشل تحميله — والصمت
  // هنا هو العطل نفسه.
  function chime() {
    if (!audio) return;
    try {
      if (audio.state === "suspended") audio.resume();
      [[660, 0], [988, 0.13]].forEach(function (pair) {
        var osc = audio.createOscillator();
        var gain = audio.createGain();
        osc.type = "sine";
        osc.frequency.value = pair[0];
        var t = audio.currentTime + pair[1];
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.2, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
        osc.connect(gain).connect(audio.destination);
        osc.start(t);
        osc.stop(t + 0.32);
      });
    } catch (e) {}
  }

  function paint(key, value) {
    var link = document.querySelector('[data-nav="' + key + '"]');
    var badge = link && link.querySelector(".badge-count");
    if (!badge) return;
    badge.textContent = value ? String(value) : "";
    if (value) badge.removeAttribute("hidden");
    else badge.setAttribute("hidden", "");
  }

  function beat() {
    fetch("/pulse", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        paint("tickets", j.tickets);
        paint("handoffs", j.handoffs);
        var total = (j.tickets || 0) + (j.handoffs || 0);
        if (known !== null && total > known) {
          chime();
          if (document.hidden) {
            unseen += total - known;
            document.title = "(" + unseen + ") " + base;
          }
        }
        known = total;
      })
      .catch(function () {});
  }

  function schedule() {
    if (timer) clearInterval(timer);
    timer = setInterval(beat, document.hidden ? HIDDEN_MS : VISIBLE_MS);
  }

  ["pointerdown", "keydown"].forEach(function (evt) {
    document.addEventListener(evt, unlock, { once: true, passive: true });
  });
  document.addEventListener("visibilitychange", function () {
    schedule();
    if (!document.hidden) { unseen = 0; document.title = base; beat(); }
  });

  beat();
  schedule();
})();
