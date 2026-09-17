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

  // رد الاستقبال يصل هنا. كل عشر ثوانٍ، وتتوقف حين تكون الصفحة مخفية حتى لا
  // نستنزف بطارية جوال في جيب صاحبه.
  function pollOnce() {
    if (document.hidden) return;
    fetch(POLL + "?after=" + lastId, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.messages || !j.messages.length) return;
        var stick = atBottom();
        j.messages.forEach(bubble);
        if (stick) toBottom();
      })
      .catch(function () {});
  }
  setInterval(pollOnce, 10000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) pollOnce();
  });

  toBottom();
})();
