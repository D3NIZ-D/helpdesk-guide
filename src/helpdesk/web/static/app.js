/* helpdesk-guide — keyboard-first behaviour.
 *
 * Design doc section 10.2: the technician is holding a phone. Every
 * action must be reachable without the mouse, and nothing here is
 * required for the page to work -- the UI is server-rendered forms, so
 * with JavaScript disabled every button still submits.
 */
"use strict";

(function () {
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ── Toast ────────────────────────────────────────────────────────
  let toastTimer = null;
  function toast(message) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  // ── Theme ────────────────────────────────────────────────────────
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem("helpdesk-theme");
    if (saved) root.dataset.theme = saved;
  } catch (_) { /* private mode: the auto theme is a fine fallback */ }

  const themeButton = $("#theme-toggle");
  if (themeButton) {
    themeButton.addEventListener("click", () => {
      const order = ["auto", "light", "dark"];
      const next = order[(order.indexOf(root.dataset.theme || "auto") + 1) % order.length];
      root.dataset.theme = next;
      try { localStorage.setItem("helpdesk-theme", next); } catch (_) {}
      toast("Tema: " + next);
    });
  }

  // ── Skip mode ────────────────────────────────────────────────────
  // "I already tried this." The step is still answered -- the technician
  // must say what the outcome was -- but it is recorded as skipped so the
  // escalation summary can distinguish "not checked" from "checked, fine".
  let skipMode = false;
  function setSkipMode(on) {
    skipMode = on;
    document.body.classList.toggle("skip-active", on);
    const hint = $("#skip-hint");
    if (hint) hint.hidden = !on;
    const form = $("#options");
    if (form) {
      let field = form.querySelector('input[name="skip"]');
      if (on && !field) {
        field = document.createElement("input");
        field.type = "hidden";
        field.name = "skip";
        field.value = "1";
        form.appendChild(field);
      } else if (!on && field) {
        field.remove();
      }
    }
  }

  const skipToggle = $("#skip-toggle");
  if (skipToggle) skipToggle.addEventListener("click", () => setSkipMode(!skipMode));

  // ── Async text panes (summary, tree) ─────────────────────────────
  async function fillFrom(el) {
    if (!el || !el.dataset.url) return;
    try {
      const response = await fetch(el.dataset.url, { credentials: "same-origin" });
      if (!response.ok) throw new Error(response.status);
      el.textContent = await response.text();
    } catch (err) {
      el.textContent = "(yüklenemedi: " + err.message + ")";
    }
  }
  fillFrom($("#summary-text"));
  fillFrom($("#tree"));

  // ── Copy the escalation summary ──────────────────────────────────
  const copyButton = $("#copy-summary");
  async function copySummary() {
    const pane = $("#summary-text");
    if (!pane) return;
    const text = pane.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      toast("Vaka özeti kopyalandı");
    } catch (_) {
      // Clipboard API needs a secure context; plain http://127.0.0.1
      // qualifies in most browsers, but not all. Select the text so the
      // technician can still copy it by hand.
      const range = document.createRange();
      range.selectNodeContents(pane);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      toast("Kopyalanamadı — metin seçildi, Ctrl+C ile kopyala");
    }
  }
  if (copyButton) copyButton.addEventListener("click", copySummary);

  // ── Feedback ─────────────────────────────────────────────────────
  $$(".feedback").forEach((button) => {
    button.addEventListener("click", async () => {
      const kind = button.dataset.kind;
      let note = null;
      if (kind === "content_error") {
        note = window.prompt("Bu adımda ne yanlış? (kısa yaz)");
        if (note === null) return;
      }
      try {
        const response = await fetch("/api/feedback", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kind: kind,
            code: button.dataset.code || "",
            node_key: button.dataset.node || null,
            note: note,
          }),
        });
        toast(response.ok ? "Geri bildirim kaydedildi, teşekkürler" : "Kaydedilemedi");
      } catch (_) {
        toast("Kaydedilemedi");
      }
    });
  });

  // ── Checklist state (guides) ─────────────────────────────────────
  // Per-viewer convenience only: a half-finished checklist should survive
  // an accidental reload during a call.
  $$(".check").forEach((box) => {
    const key = "helpdesk-check:" + box.dataset.key;
    try {
      if (localStorage.getItem(key) === "1") box.checked = true;
    } catch (_) {}
    box.addEventListener("change", () => {
      try {
        if (box.checked) localStorage.setItem(key, "1");
        else localStorage.removeItem(key);
      } catch (_) {}
    });
  });

  // ── Elapsed timer ────────────────────────────────────────────────
  const timer = $("#timer");
  if (timer) {
    let seconds = parseInt(timer.dataset.elapsed || "0", 10) || 0;
    const tick = () => {
      const m = String(Math.floor(seconds / 60)).padStart(2, "0");
      const s = String(seconds % 60).padStart(2, "0");
      timer.textContent = "⏱ " + m + ":" + s;
      seconds += 1;
    };
    tick();
    setInterval(tick, 1000);
  }

  // ── Keyboard ─────────────────────────────────────────────────────
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA"
                              || target.tagName === "SELECT" || target.isContentEditable);

    if (event.key === "/" && !typing) {
      const search = $("#q");
      if (search) { event.preventDefault(); search.focus(); search.select(); }
      return;
    }

    if (event.key === "Escape") {
      const search = $("#q");
      if (typing && target === search) { search.value = ""; return; }
      if (skipMode) { setSkipMode(false); return; }
      return;
    }

    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;

    // 1-9 picks an option on a walk, or a result on the search page.
    if (event.key >= "1" && event.key <= "9") {
      const option = $('.option[data-index="' + event.key + '"]');
      if (option) { event.preventDefault(); option.click(); return; }
      const result = $('.results a[data-index="' + event.key + '"]');
      if (result) { event.preventDefault(); result.click(); return; }
    }

    if (event.key === "Backspace") {
      const back = $('form[action$="/back"] button');
      if (back) { event.preventDefault(); back.click(); }
      return;
    }

    if (event.key === "s" && skipToggle) { event.preventDefault(); setSkipMode(!skipMode); return; }
    if (event.key === "c" && copyButton) { event.preventDefault(); copySummary(); }
  });
})();
