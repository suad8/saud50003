// صفحة النزيل: اختيار اللغة، إرسال الرسائل، ومتابعة رد الاستقبال.
// بلا مكتبات — الصفحة تُفتح على جوال قد يكون على شبكة حرم مزدحمة.
(function () {
  "use strict";

  // --- اختيار اللغة في صفحة الدخول ---
  var langs = document.querySelectorAll(".lang");
  var langField = document.getElementById("lang");
  if (langs.length && langField) {
    langs.forEach(function (b) {
      b.addEventListener("click", function () {
        langs.forEach(function (o) { o.setAttribute("aria-pressed", "false"); });
        b.setAttribute("aria-pressed", "true");
        langField.value = b.dataset.lang;
        document.documentElement.lang = b.dataset.lang;
        document.documentElement.dir =
          ["en", "id", "tr", "fr", "ms", "ha"].indexOf(b.dataset.lang) >= 0 ? "ltr" : "rtl";
      });
    });
  }

  // --- رقم التجربة في وضع العرض ---
  var demoNum = document.querySelector(".demo-num");
  var phoneField = document.getElementById("phone");
  if (demoNum && phoneField) {
    demoNum.addEventListener("click", function () {
      phoneField.value = demoNum.dataset.phone;
      phoneField.focus();
    });
  }

  // --- المحادثة ---
  var chat = document.getElementById("chat");
  var form = document.getElementById("f");
  var input = document.getElementById("t");
  if (!chat || !form || !input) return;

  var script = document.querySelector('script[data-send]');
  var SEND = script.dataset.send;
  var POLL = script.dataset.poll;
  // آخر رسالة صادرة عُرضت من الخادم. بدونها يعيد أول استطلاع كل المحادثة.
  var lastId = parseInt(chat.dataset.last || "0", 10) || 0;
  var busy = false;

  function atBottom() {
    return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 60;
  }
  function toBottom() { chat.scrollTop = chat.scrollHeight; }

  function bubble(m) {
    var wrap = document.createElement("div");
    wrap.className = "msg " + (m.dir === "in" ? "me" : "them");
    var b = document.createElement("div");
    b.className = "bub" + (m.staff ? " staff" : "");
    if (m.by) {
      var who = document.createElement("span");
      who.className = "from";
      who.textContent = m.by;
      b.appendChild(who);
    }
    b.appendChild(document.createTextNode(m.text));
    wrap.appendChild(b);
    var at = document.createElement("span");
    at.className = "at";
    at.textContent = m.at || "";
    wrap.appendChild(at);
    if (m.ticket) {
      var t = document.createElement("span");
      t.className = "tick";
      t.textContent = "✓ وصل طلبك للفريق";
      wrap.appendChild(t);
    }
    chat.appendChild(wrap);
    if (m.id && m.id > lastId) lastId = m.id;
  }

  function note(text) {
    var p = document.createElement("p");
    p.className = "sys";
    p.textContent = text;
    chat.appendChild(p);
    toBottom();
  }

  function typing(on) {
    var old = document.getElementById("typing");
    if (old) old.remove();
    if (!on) return;
    var p = document.createElement("p");
    p.id = "typing";
    p.className = "sys";
    p.textContent = "…";
    chat.appendChild(p);
    toBottom();
  }

  function lock(on) {
    busy = on;
    input.disabled = on;
    document.querySelectorAll(".q").forEach(function (q) { q.disabled = on; });
  }

  function say(text, shortcut) {
    if (busy || !text.trim()) return;
    lock(true);
    bubble({ dir: "in", text: text, at: "" });
    toBottom();
    typing(true);

    var body = new URLSearchParams();
    body.set("text", text);
    if (shortcut) body.set("shortcut", shortcut);
    fetch(SEND, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        typing(false);
        if (!res.ok) {
          note(res.j.text || "ما قدرنا نرسل رسالتك.");
          if (res.j.error === "no_active_stay" || res.j.error === "device_revoked") {
            lock(true);
            return;
          }
          lock(false);
          return;
        }
        (res.j.messages || []).forEach(function (m) { if (m.dir === "out") bubble(m); });
        toBottom();
        lock(false);
      })
      .catch(function () {
        typing(false);
        note("الشبكة ما استجابت. جرّب مرة ثانية.");
        lock(false);
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = input.value;
    input.value = "";
    say(v);
  });

  document.querySelectorAll(".q").forEach(function (q) {
    q.addEventListener("click", function () { say(q.dataset.say, q.dataset.key); });
  });

  // ---------- التنبيه حين يكون النزيل خارج الصفحة ----------
  //
  // النزيل يسأل ثم يخرج من المتصفح ينتظر. فالرد الذي يصل بلا صوت لا يصل
  // فعلًا: يجده بعد ساعة، ويكون الموظف قد انتظر ردًّا لم يأتِ.

  var audio = null;
  var unheard = 0;
  var baseTitle = document.title;

  // النغمة مولَّدة لا ملفًّا: لا طلب ثانيًا على شبكة فندق بطيئة، ولا صمتًا
  // حين يفشل تحميل الملف — والصمت هنا هو العطل نفسه الذي نعالجه.
  function unlock() {
    if (audio) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try {
      audio = new Ctx();
      if (audio.state === "suspended") audio.resume();
    } catch (e) { audio = null; }
  }

  function chime() {
    if (!audio) return;
    try {
      if (audio.state === "suspended") audio.resume();
      // نغمتان صاعدتان قصيرتان: تُسمع في ردهة فندق ولا تزعج في غرفة.
      [[880, 0], [1175, 0.12]].forEach(function (pair) {
        var osc = audio.createOscillator();
        var gain = audio.createGain();
        osc.type = "sine";
        osc.frequency.value = pair[0];
        var start = audio.currentTime + pair[1];
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.22, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.28);
        osc.connect(gain).connect(audio.destination);
        osc.start(start);
        osc.stop(start + 0.3);
      });
    } catch (e) {}
  }

  function badge() {
    document.title = unheard ? "(" + unheard + ") " + baseTitle : baseTitle;
  }

  function popup(text) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      var n = new Notification(baseTitle, { body: text, tag: "daif-reply", renotify: true });
      n.onclick = function () { window.focus(); n.close(); };
    } catch (e) {}
  }

  function alertGuest(messages) {
    var staff = messages.filter(function (m) { return m.staff; });
    if (!staff.length) return;
    chime();
    if (document.hidden) {
      unheard += staff.length;
      badge();
      popup(staff[staff.length - 1].text);
    }
  }

  // الإذن يُطلب بعد أول رسالة يرسلها النزيل، لا عند فتح الصفحة: طلبٌ قبل أن
  // يفعل شيئًا يُرفض غالبًا، والرفض نهائي لا يُسأل بعده.
  function askOnce() {
    if (!("Notification" in window) || Notification.permission !== "default") return;
    try { Notification.requestPermission(); } catch (e) {}
  }

  ["pointerdown", "keydown"].forEach(function (evt) {
    document.addEventListener(evt, unlock, { once: true, passive: true });
  });
  form.addEventListener("submit", askOnce, { once: true });

  // ردّ الاستقبال يصل هنا. نواصل الاستطلاع والصفحة مخفية — وهذا هو مربط
  // الفرس: نزيل خرج من المتصفح لن يرى شيئًا إن توقّفنا. لكن بوتيرة أبطأ،
  // فجوالٌ في جيب صاحبه لا يُستنزف من أجل محادثة ساكنة.
  var VISIBLE_MS = 10000;
  var HIDDEN_MS = 30000;
  var timer = null;

  function pollOnce() {
    fetch(POLL + "?after=" + lastId, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.messages || !j.messages.length) return;
        var stick = atBottom();
        j.messages.forEach(bubble);
        if (stick) toBottom();
        alertGuest(j.messages);
      })
      .catch(function () {});
  }

  function schedule() {
    if (timer) clearInterval(timer);
    timer = setInterval(pollOnce, document.hidden ? HIDDEN_MS : VISIBLE_MS);
  }

  schedule();
  document.addEventListener("visibilitychange", function () {
    schedule();
    if (!document.hidden) {
      unheard = 0;
      badge();
      pollOnce();
    }
  });

  toBottom();
})();
