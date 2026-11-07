(() => {
  "use strict";
  const screen = document.getElementById("terminal-private");
  if (!screen) return;
  const maxIdle = 15000;
  let lastActivity = Date.now(), lastSent = 0, pending = null, locked = false;
  const sequence = screen.dataset.sequence;
  const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
  const counter = document.getElementById("idle-counter");
  function lock() {
    if (locked) return;
    locked = true;
    screen.replaceChildren();
    window.location.replace(`/terminal/?locked=1&sequence=${encodeURIComponent(sequence)}`);
  }
  async function refresh() {
    if (locked || document.hidden) return;
    lastSent = Date.now();
    try {
      const response = await fetch("/terminal/activity/", {method: "POST", credentials: "same-origin", headers: {"X-CSRFToken": csrf}});
      if (response.status === 401 || response.status === 403) lock();
      else if (response.ok) { const state = await response.json(); if (String(state.sequence) !== sequence) lock(); }
    } catch (_) { /* 断网不排队、不伪造成功；后台已开工任务继续计时。 */ }
  }
  function touched(event) {
    if (!event.isTrusted || locked) return;
    lastActivity = Date.now();
    clearTimeout(pending);
    if (lastActivity - lastSent >= 1000) refresh();
    else pending = setTimeout(refresh, 200);
  }
  ["pointerdown", "keydown", "input", "touchstart"].forEach(name => document.addEventListener(name, touched, {passive: true}));
  setInterval(() => {
    const remaining = maxIdle - (Date.now() - lastActivity);
    if (counter) counter.textContent = String(Math.max(0, Math.ceil(remaining / 1000)));
    if (remaining <= 0) lock();
  }, 250);
  window.addEventListener("pagehide", () => { screen.replaceChildren(); });
  window.addEventListener("pageshow", event => { if (event.persisted) lock(); });
  document.addEventListener("visibilitychange", async () => {
    screen.style.visibility = "hidden";
    if (!document.hidden) {
      if (Date.now() - lastActivity >= maxIdle) { lock(); return; }
      try {
        const response = await fetch("/terminal/status/", {credentials: "same-origin", cache: "no-store"});
        const state = await response.json();
        if (!state.active || String(state.sequence) !== sequence) lock();
        else screen.style.visibility = "visible";
      } catch (_) { lock(); }
    }
  });
  if (typeof BroadcastChannel === "function") {
    const channel = new BroadcastChannel("terminal-identity");
    channel.onmessage = event => { if (String(event.data) !== sequence) lock(); };
    channel.postMessage(sequence);
  }
  const clock = document.querySelector("[data-elapsed]");
  if (clock) {
    const started = performance.now(), base = Number(clock.dataset.elapsed), running = clock.dataset.running === "true";
    setInterval(() => {
      const seconds = base + (running ? Math.floor((performance.now() - started) / 1000) : 0);
      clock.textContent = `${Math.floor(seconds / 3600)} 时 ${Math.floor(seconds % 3600 / 60)} 分 ${seconds % 60} 秒`;
    }, 1000);
  }
  const network = document.getElementById("network-state");
  function connectivity() {
    const offline = !navigator.onLine;
    if (network) network.textContent = offline ? "网络已断开，操作须联网；已开工任务继续计时。" : "";
    screen.querySelectorAll("button[data-online]").forEach(button => { button.disabled = offline; });
  }
  window.addEventListener("online", connectivity);
  window.addEventListener("offline", connectivity);
  connectivity();
})();
