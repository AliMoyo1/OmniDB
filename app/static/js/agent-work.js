(() => {
  "use strict";
  const startedAt = Date.now();
  const card = document.querySelector("[data-lease-expires]");
  const disposition = document.getElementById("disposition-id");
  const completeForm = document.getElementById("disposition-form");
  const callbackField = document.getElementById("callback-field");
  const callbackInput = document.getElementById("callback-at");
  const notes = document.getElementById("notes");
  const notesHint = document.getElementById("notes-hint");
  const dncWarning = document.getElementById("dnc-warning");

  function syncDisposition() {
    if (!disposition) return;
    const option = disposition.options[disposition.selectedIndex];
    const needsNotes = option?.dataset.notes === "true";
    const needsCallback = option?.dataset.callback === "true";
    const causesDnc = option?.dataset.dnc === "true";
    notes.required = needsNotes;
    notesHint.textContent = needsNotes ? "Required for this outcome" : "Optional unless required by the selected outcome";
    callbackField.hidden = !needsCallback;
    callbackInput.required = needsCallback;
    dncWarning.hidden = !causesDnc;
  }
  disposition?.addEventListener("change", syncDisposition);
  syncDisposition();

  if (card) {
    const expiresAt = Date.parse(card.dataset.leaseExpires);
    const countdown = document.getElementById("lease-countdown");
    const submit = document.getElementById("complete-contact");
    const duration = document.getElementById("duration-seconds");
    const tick = () => {
      const remaining = Math.max(0, expiresAt - Date.now());
      const totalSeconds = Math.ceil(remaining / 1000);
      countdown.textContent = `${String(Math.floor(totalSeconds / 60)).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`;
      card.classList.toggle("lease-warning", remaining > 0 && remaining <= 120000);
      if (remaining === 0) {
        card.classList.add("lease-expired");
        countdown.textContent = "EXPIRED";
        submit.disabled = true;
      }
      duration.value = String(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    };
    tick();
    window.setInterval(tick, 1000);
  }

  document.addEventListener("keydown", (event) => {
    if (!event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === "n") document.getElementById("next-contact-form")?.requestSubmit();
    if (key === "s") disposition?.focus();
    if (event.key === "Enter") completeForm?.requestSubmit();
    if (key === "k") document.getElementById("skip-reason")?.focus();
  });
})();
