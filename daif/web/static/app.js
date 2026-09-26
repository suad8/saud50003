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
