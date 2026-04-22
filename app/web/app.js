/* ------------------------------------------------------------------ */
/* Heating Brain — multi-page SPA                                     */
/* vanilla JS, no framework, no build step                            */
/* ------------------------------------------------------------------ */

"use strict";

// ------------------------------------------------------------------ //
// Utilities                                                           //
// ------------------------------------------------------------------ //

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k === "style") e.style.cssText = v;
      else e.setAttribute(k, v);
    }
  }
  if (children) {
    for (const c of children) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
  }
  return e;
}

function fmtTemp(v) {
  return (v != null) ? v.toFixed(1) + " °C" : "—";
}

function relativeTime(epochSeconds) {
  if (!epochSeconds) return null;
  const diffMs = Date.now() - epochSeconds * 1000;
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 5)  return "just now";
  if (diffSec < 60) return diffSec + "s ago";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return diffMin + "m ago";
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24)  return diffHr + "h ago";
  const diffDay = Math.floor(diffHr / 24);
  return diffDay + "d ago";
}

// ------------------------------------------------------------------ //
// PIN screen                                                          //
// ------------------------------------------------------------------ //

const PinScreen = (() => {
  let entered = "";

  function update() {
    const disp = document.getElementById("pin-display");
    disp.textContent = "●".repeat(entered.length) + "○".repeat(Math.max(0, 4 - entered.length));
  }

  function showError(msg) {
    const el2 = document.getElementById("pin-error");
    el2.textContent = msg;
    el2.classList.remove("hidden");
    setTimeout(() => el2.classList.add("hidden"), 2000);
  }

  async function submit() {
    if (entered.length === 0) return;
    const pin = entered;
    entered = "";
    update();
    try {
      const resp = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      if (resp.ok) {
        showApp();
      } else {
        showError("Incorrect PIN");
      }
    } catch (e) {
      showError("Network error");
    }
  }

  function init() {
    document.querySelectorAll(".pin-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const digit = btn.dataset.digit;
        const action = btn.dataset.action;
        if (digit !== undefined) {
          if (entered.length < 4) {
            entered += digit;
            update();
            if (entered.length === 4) submit();
          }
        } else if (action === "clear") {
          entered = entered.slice(0, -1);
          update();
        } else if (action === "submit") {
          submit();
        }
      });
    });
  }

  return { init };
})();

// ------------------------------------------------------------------ //
// Screen / page switching                                             //
// ------------------------------------------------------------------ //

function showPinScreen() {
  document.getElementById("pin-screen").classList.remove("hidden");
  document.getElementById("app-screen").classList.add("hidden");
}

function showApp() {
  document.getElementById("pin-screen").classList.add("hidden");
  document.getElementById("app-screen").classList.remove("hidden");
  App.init();
}

// ------------------------------------------------------------------ //
// Hash router                                                         //
// ------------------------------------------------------------------ //

const Router = (() => {
  const PAGES = ["now", "history", "schedule", "settings"];

  function currentPage() {
    const hash = location.hash.replace(/^#\//, "");
    return PAGES.includes(hash) ? hash : "now";
  }

  function navigate() {
    const page = currentPage();
    PAGES.forEach(function(p) {
      const pageEl = document.getElementById("page-" + p);
      if (pageEl) {
        if (p === page) {
          pageEl.classList.remove("hidden");
        } else {
          pageEl.classList.add("hidden");
        }
      }
    });

    // Update active tab
    document.querySelectorAll(".tab-item").forEach(function(tab) {
      if (tab.dataset.page === page) {
        tab.classList.add("active");
      } else {
        tab.classList.remove("active");
      }
    });

    // Lazy-load page content
    if (page === "history") {
      Chart.loadBoth();
    } else if (page === "schedule") {
      Schedule.load();
    } else if (page === "settings") {
      Updater.init();
    }
  }

  function init() {
    window.addEventListener("hashchange", navigate);
    navigate();
  }

  return { init, currentPage };
})();

// ------------------------------------------------------------------ //
// API helpers                                                         //
// ------------------------------------------------------------------ //

async function apiFetch(url, options) {
  const resp = await fetch(url, options || {});
  if (resp.status === 401) {
    showPinScreen();
    throw new Error("unauthorized");
  }
  return resp;
}

// ------------------------------------------------------------------ //
// Status poller                                                       //
// ------------------------------------------------------------------ //

const Status = (() => {
  let timer = null;
  let _lastData = null;

  function applyNowPage(data) {
    // Status header card
    const state = (data.commanded_state || "unknown").toLowerCase();
    const headingEl = document.getElementById("st-status-label");
    const badgeEl = document.getElementById("st-heating");
    if (headingEl) headingEl.textContent = "STATUS";
    if (badgeEl) {
      badgeEl.textContent = state.toUpperCase();
      badgeEl.className = "value badge " + (state === "on" ? "on" : state === "off" ? "off" : "unknown");
    }

    // Temperatures
    const indoorEl = document.getElementById("st-indoor");
    const outdoorEl = document.getElementById("st-outdoor");
    if (indoorEl) indoorEl.textContent = fmtTemp(data.indoor_temp_c);
    if (outdoorEl) outdoorEl.textContent = fmtTemp(data.outdoor_temp_c);

    // Last command
    const lastCmdHeader = document.getElementById("lastcmd-header");
    const reasonEl = document.getElementById("st-reason");
    if (lastCmdHeader) {
      const rel = relativeTime(data.last_tado_command_at);
      lastCmdHeader.textContent = rel ? "LAST COMMAND — " + rel : "LAST COMMAND";
    }
    if (reasonEl) reasonEl.textContent = data.last_reason || "—";

    // Control buttons active state
    applyControlButtons(data);
  }

  function applyControlButtons(data) {
    const btnOn   = document.getElementById("btn-force-on");
    const btnOff  = document.getElementById("btn-force-off");
    const btnAuto = document.getElementById("btn-resume");
    if (!btnOn) return;

    // Auto is active when no override is in force.
    // On/Off reflect the *current* commanded heating state, so the user can
    // see both what mode they're in AND what the heating is currently doing.
    const state = (data.commanded_state || "unknown").toLowerCase();
    const autoActive = !data.override_active;
    const onActive   = state === "on";
    const offActive  = state === "off";

    btnOn.classList.toggle("ctrl-active", onActive);
    btnOn.classList.toggle("ctrl-dull",   !onActive);
    btnOff.classList.toggle("ctrl-active", offActive);
    btnOff.classList.toggle("ctrl-dull",   !offActive);
    btnAuto.classList.toggle("ctrl-active", autoActive);
    btnAuto.classList.toggle("ctrl-dull",   !autoActive);
  }

  function apply(data) {
    _lastData = data;
    applyNowPage(data);
    NextSchedule.update(data);
  }

  async function poll() {
    try {
      const resp = await apiFetch("/api/status");
      if (resp.ok) apply(await resp.json());
    } catch (e) {
      if (e.message !== "unauthorized") console.warn("Status poll error:", e);
    }
  }

  function start() {
    poll();
    timer = setInterval(poll, 10000);
  }

  function stop() {
    if (timer) clearInterval(timer);
  }

  function getData() { return _lastData; }

  return { start, stop, poll, getData };
})();

// ------------------------------------------------------------------ //
// Override / control buttons                                          //
// ------------------------------------------------------------------ //

const Override = (() => {
  function showFeedback(msg, ok) {
    const feedEl = document.getElementById("override-feedback");
    if (!feedEl) return;
    feedEl.textContent = msg;
    feedEl.className = "feedback " + (ok ? "ok" : "err");
    feedEl.classList.remove("hidden");
    setTimeout(() => feedEl.classList.add("hidden"), 2500);
  }

  async function send(mode) {
    // Optimistic UI update
    const fakeOverride = {
      override_active: mode !== "auto",
      override_mode: mode !== "auto" ? mode : null,
    };
    const current = Status.getData() || {};
    const fakeData = Object.assign({}, current, fakeOverride);
    // Apply just the button state optimistically
    const btnOn   = document.getElementById("btn-force-on");
    const btnOff  = document.getElementById("btn-force-off");
    const btnAuto = document.getElementById("btn-resume");
    if (btnOn) {
      // Optimistic: assume the chosen mode takes effect.
      // On → state becomes on + not-auto. Off → state off + not-auto. Auto → keep current state + auto.
      const currentState = ((Status.getData() || {}).commanded_state || "").toLowerCase();
      const onActive   = mode === "on"  || (mode === "auto" && currentState === "on");
      const offActive  = mode === "off" || (mode === "auto" && currentState === "off");
      const autoActive = mode === "auto";
      btnOn.classList.toggle("ctrl-active", onActive);
      btnOn.classList.toggle("ctrl-dull",   !onActive);
      btnOff.classList.toggle("ctrl-active", offActive);
      btnOff.classList.toggle("ctrl-dull",   !offActive);
      btnAuto.classList.toggle("ctrl-active", autoActive);
      btnAuto.classList.toggle("ctrl-dull",   !autoActive);
    }

    try {
      const resp = await apiFetch("/api/override", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (resp.ok) {
        Status.poll();
      } else {
        const d = await resp.json();
        showFeedback(d.error || "Error", false);
        Status.poll(); // revert optimistic
      }
    } catch (e) {
      if (e.message !== "unauthorized") showFeedback("Network error", false);
      Status.poll();
    }
  }

  function init() {
    document.getElementById("btn-force-on").addEventListener("click",  () => send("on"));
    document.getElementById("btn-force-off").addEventListener("click", () => send("off"));
    document.getElementById("btn-resume").addEventListener("click",    () => send("auto"));
  }

  return { init };
})();

// ------------------------------------------------------------------ //
// Next schedule helper (computed client-side from /api/schedule)     //
// ------------------------------------------------------------------ //

const NextSchedule = (() => {
  let _windows = [];

  const DAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
  const DAY_LABELS = { sun:"Sunday", mon:"Monday", tue:"Tuesday", wed:"Wednesday",
                       thu:"Thursday", fri:"Friday", sat:"Saturday" };

  function timeToMinutes(hhmm) {
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + m;
  }

  function minutesToHHMM(mins) {
    const h = Math.floor(((mins % 1440) + 1440) % 1440 / 60);
    const m = ((mins % 1440) + 1440) % 1440 % 60;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  }

  function compute() {
    if (!_windows || _windows.length === 0) return null;
    const now = new Date();
    const todayDayIdx = now.getDay(); // 0=Sun
    const todayName = DAY_NAMES[todayDayIdx];
    const nowMins = now.getHours() * 60 + now.getMinutes();

    // Check if any window is currently active
    for (const w of _windows) {
      if (!w.days.includes(todayName)) continue;
      const start = timeToMinutes(w.start);
      const end   = timeToMinutes(w.end);
      const wraps = end <= start; // midnight-spanning window
      const active = wraps
        ? (nowMins >= start || nowMins < end)
        : (nowMins >= start && nowMins < end);
      if (active) {
        // Currently active — find end time
        let endStr;
        if (wraps && nowMins >= start) {
          endStr = minutesToHHMM(end);
        } else {
          endStr = minutesToHHMM(end);
        }
        return "Currently: " + w.name + " — ends at " + endStr;
      }
    }

    // No active window — find next
    let best = null;
    let bestMinsFromNow = Infinity;

    for (const w of _windows) {
      const start = timeToMinutes(w.start);
      for (let dayOffset = 0; dayOffset < 7; dayOffset++) {
        const checkDayIdx = (todayDayIdx + dayOffset) % 7;
        const checkDayName = DAY_NAMES[checkDayIdx];
        if (!w.days.includes(checkDayName)) continue;
        const minsFromNow = dayOffset * 1440 + start - nowMins;
        if (minsFromNow <= 0 && dayOffset === 0) continue; // already passed today
        const adjusted = minsFromNow <= 0 ? minsFromNow + 7 * 1440 : minsFromNow;
        if (adjusted < bestMinsFromNow) {
          bestMinsFromNow = adjusted;
          best = { w, dayOffset: dayOffset === 0 && start > nowMins ? 0 : dayOffset, dayName: checkDayName, start };
        }
      }
    }

    if (!best) return "No schedule configured.";

    const startStr = minutesToHHMM(best.start);
    if (best.dayOffset === 0) {
      return "Next: " + best.w.name + " — starts at " + startStr + " today";
    } else if (best.dayOffset === 1) {
      return "Next: " + best.w.name + " — starts at " + startStr + " tomorrow";
    } else {
      return "Next: " + best.w.name + " — starts at " + startStr + " on " + DAY_LABELS[best.dayName];
    }
  }

  function update() {
    const el2 = document.getElementById("next-schedule-text");
    if (!el2) return;
    const text = compute();
    el2.textContent = text || "No schedule configured.";
  }

  function setWindows(windows) {
    _windows = windows || [];
    update();
  }

  return { setWindows, update };
})();

// ------------------------------------------------------------------ //
/* History chart (inline SVG)                                         */
// ------------------------------------------------------------------ //

const Chart = (() => {
  const W = 800;
  const H = 200;
  const PAD = { top: 16, right: 8, bottom: 8, left: 8 };

  function svgEl(tag, attrs) {
    const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    }
    return e;
  }

  function polyline(points, stroke, width) {
    const pts = points.map(function(p) { return p[0] + "," + p[1]; }).join(" ");
    return svgEl("polyline", {
      points: pts,
      fill: "none",
      stroke: stroke,
      "stroke-width": width || 2,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    });
  }

  function downsample(samples, maxPoints) {
    if (samples.length <= maxPoints) return samples;
    const step = samples.length / maxPoints;
    const result = [];
    for (let i = 0; i < maxPoints; i++) {
      result.push(samples[Math.round(i * step)]);
    }
    return result;
  }

  function render(svgId, samples) {
    const svg = document.getElementById(svgId);
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    if (!samples || samples.length < 2) {
      const t = svgEl("text", {
        x: W / 2, y: H / 2,
        "text-anchor": "middle",
        fill: "#8890b0",
        "font-size": "24",
      });
      t.textContent = "No data yet";
      svg.appendChild(t);
      return;
    }

    const chartW = W - PAD.left - PAD.right;
    const chartH = H - PAD.top - PAD.bottom;

    const times = samples.map(function(s) { return s.ts; });
    const tMin = Math.min.apply(null, times);
    const tMax = Math.max.apply(null, times);
    const tRange = tMax - tMin || 1;

    const temps = [];
    samples.forEach(function(s) {
      if (s.indoor_temp_c != null)  temps.push(s.indoor_temp_c);
      if (s.outdoor_temp_c != null) temps.push(s.outdoor_temp_c);
    });
    let tempMin = temps.length ? Math.min.apply(null, temps) - 1 : 0;
    let tempMax = temps.length ? Math.max.apply(null, temps) + 1 : 30;
    if (tempMax - tempMin < 4) { tempMin -= 2; tempMax += 2; }
    const tempRange = tempMax - tempMin;

    function tx(ts) { return PAD.left + ((ts - tMin) / tRange) * chartW; }
    function ty(t)  { return PAD.top + chartH - ((t - tempMin) / tempRange) * chartH; }

    // Heating state background bars
    let heatingStart = null;
    for (let i = 0; i < samples.length; i++) {
      const s = samples[i];
      if (s.heating_on && heatingStart === null) {
        heatingStart = s.ts;
      } else if (!s.heating_on && heatingStart !== null) {
        svg.appendChild(svgEl("rect", {
          x: tx(heatingStart), y: PAD.top,
          width: tx(s.ts) - tx(heatingStart), height: chartH,
          fill: "rgba(46,204,113,0.15)",
        }));
        heatingStart = null;
      }
    }
    if (heatingStart !== null) {
      svg.appendChild(svgEl("rect", {
        x: tx(heatingStart), y: PAD.top,
        width: tx(tMax) - tx(heatingStart), height: chartH,
        fill: "rgba(46,204,113,0.15)",
      }));
    }

    // Indoor line
    const indoorPts = samples
      .filter(function(s) { return s.indoor_temp_c != null; })
      .map(function(s)   { return [tx(s.ts), ty(s.indoor_temp_c)]; });
    if (indoorPts.length > 1) svg.appendChild(polyline(indoorPts, "#4f8ef7"));

    // Outdoor line
    const outdoorPts = samples
      .filter(function(s) { return s.outdoor_temp_c != null; })
      .map(function(s)   { return [tx(s.ts), ty(s.outdoor_temp_c)]; });
    if (outdoorPts.length > 1) svg.appendChild(polyline(outdoorPts, "#f39c12"));
  }

  async function load24() {
    try {
      const resp = await apiFetch("/api/history?hours=24");
      if (resp.ok) render("history-chart-24", await resp.json());
    } catch (e) {
      if (e.message !== "unauthorized") console.warn("History load error:", e);
    }
  }

  async function loadWeek() {
    try {
      const resp = await apiFetch("/api/history?hours=168");
      if (resp.ok) {
        const raw = await resp.json();
        // Downsample to ~200 points for week view (one per ~50 min)
        render("history-chart-week", downsample(raw, 200));
      }
    } catch (e) {
      if (e.message !== "unauthorized") console.warn("Week history load error:", e);
    }
  }

  function loadBoth() {
    load24();
    loadWeek();
  }

  return { loadBoth, load24 };
})();

// ------------------------------------------------------------------ //
// Schedule CRUD                                                       //
// ------------------------------------------------------------------ //

const Schedule = (() => {
  let windows = [];

  const DAY_ORDER = ["mon","tue","wed","thu","fri","sat","sun"];
  const DAY_LABEL = { mon:"Mon", tue:"Tue", wed:"Wed", thu:"Thu", fri:"Fri", sat:"Sat", sun:"Sun" };

  function formatDays(days) {
    const set = new Set(days);
    if (set.size === 7) return "Every day";
    if (set.size === 5 && ["mon","tue","wed","thu","fri"].every(function(d) { return set.has(d); })) return "Weekdays";
    if (set.size === 2 && ["sat","sun"].every(function(d) { return set.has(d); })) return "Weekends";
    return DAY_ORDER.filter(function(d) { return set.has(d); }).map(function(d) { return DAY_LABEL[d]; }).join(", ");
  }

  function render() {
    const container = document.getElementById("schedule-list");
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (windows.length === 0) {
      container.appendChild(el("p", { style: "color:var(--text-muted);font-size:0.9rem;margin-bottom:10px" }, ["No windows configured."]));
      return;
    }

    windows.forEach(function(w, idx) {
      const nameEl = el("div", { class: "window-name" }, [w.name]);
      const metaEl = el("div", { class: "window-meta" });
      metaEl.appendChild(document.createTextNode(
        formatDays(w.days) + " • " + w.start + "–" + w.end
      ));
      metaEl.appendChild(document.createElement("br"));
      metaEl.appendChild(document.createTextNode(
        "ON: indoor <" + w.indoor_on_celsius + "°C & outdoor <" + w.outdoor_on_celsius + "°C"
      ));
      metaEl.appendChild(document.createElement("br"));
      metaEl.appendChild(document.createTextNode(
        "OFF: indoor >" + w.indoor_off_celsius + "°C & outdoor >" + w.outdoor_off_celsius + "°C"
      ));

      const infoEl = el("div", { class: "window-info" }, [nameEl, metaEl]);
      const editBtn = el("button", { class: "window-edit-btn", "data-idx": String(idx) }, ["Edit"]);
      editBtn.addEventListener("click", function() { WindowModal.open(idx); });

      container.appendChild(el("div", { class: "window-item" }, [infoEl, editBtn]));
    });
  }

  async function load() {
    try {
      const resp = await apiFetch("/api/schedule");
      if (resp.ok) {
        windows = await resp.json();
        render();
        NextSchedule.setWindows(windows);
      }
    } catch (e) {
      if (e.message !== "unauthorized") console.warn("Schedule load error:", e);
    }
  }

  async function save(newWindows) {
    const resp = await apiFetch("/api/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newWindows),
    });
    if (!resp.ok) {
      const d = await resp.json();
      throw new Error(d.error || "Save failed");
    }
    windows = newWindows;
    render();
    NextSchedule.setWindows(windows);
  }

  function getWindows() { return windows; }

  return { load, save, render, getWindows };
})();

// ------------------------------------------------------------------ //
// Window edit modal                                                   //
// ------------------------------------------------------------------ //

const WindowModal = (() => {
  const DAY_ORDER = ["mon","tue","wed","thu","fri","sat","sun"];

  function getCheckedDays() {
    return Array.from(document.querySelectorAll("input[name=\"day\"]:checked")).map(function(cb) { return cb.value; });
  }

  function setCheckedDays(days) {
    document.querySelectorAll("input[name=\"day\"]").forEach(function(cb) {
      cb.checked = days.indexOf(cb.value) !== -1;
    });
  }

  function showError(msg) {
    const errEl = document.getElementById("wf-error");
    errEl.textContent = msg;
    errEl.classList.remove("hidden");
  }

  function hideError() {
    document.getElementById("wf-error").classList.add("hidden");
  }

  function open(idx) {
    hideError();
    const wins = Schedule.getWindows();
    const isNew = idx === -1;
    document.getElementById("modal-title").textContent = isNew ? "Add Window" : "Edit Window";
    document.getElementById("wf-index").value = idx;
    document.getElementById("wf-delete").classList.toggle("hidden", isNew);

    if (isNew) {
      document.getElementById("wf-name").value = "";
      setCheckedDays(DAY_ORDER);
      document.getElementById("wf-start").value = "06:30";
      document.getElementById("wf-end").value = "09:00";
      document.getElementById("wf-indoor-on").value = "18";
      document.getElementById("wf-outdoor-on").value = "15";
      document.getElementById("wf-indoor-off").value = "20";
      document.getElementById("wf-outdoor-off").value = "17";
    } else {
      const w = wins[idx];
      document.getElementById("wf-name").value = w.name;
      setCheckedDays(w.days);
      document.getElementById("wf-start").value = w.start;
      document.getElementById("wf-end").value = w.end;
      document.getElementById("wf-indoor-on").value = w.indoor_on_celsius;
      document.getElementById("wf-outdoor-on").value = w.outdoor_on_celsius;
      document.getElementById("wf-indoor-off").value = w.indoor_off_celsius;
      document.getElementById("wf-outdoor-off").value = w.outdoor_off_celsius;
    }

    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("wf-name").focus();
  }

  function close() {
    document.getElementById("modal-overlay").classList.add("hidden");
  }

  function buildWindow() {
    const days = getCheckedDays();
    if (days.length === 0) throw new Error("Select at least one day.");
    const name = document.getElementById("wf-name").value.trim();
    if (!name) throw new Error("Name is required.");
    return {
      name: name,
      days: days,
      start: document.getElementById("wf-start").value,
      end: document.getElementById("wf-end").value,
      indoor_on_celsius: parseFloat(document.getElementById("wf-indoor-on").value),
      outdoor_on_celsius: parseFloat(document.getElementById("wf-outdoor-on").value),
      indoor_off_celsius: parseFloat(document.getElementById("wf-indoor-off").value),
      outdoor_off_celsius: parseFloat(document.getElementById("wf-outdoor-off").value),
    };
  }

  function init() {
    document.getElementById("wf-cancel").addEventListener("click", close);

    document.getElementById("modal-overlay").addEventListener("click", function(e) {
      if (e.target === document.getElementById("modal-overlay")) close();
    });

    document.querySelectorAll(".preset-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        const preset = btn.dataset.preset;
        const all = DAY_ORDER;
        const weekdays = ["mon","tue","wed","thu","fri"];
        const weekends = ["sat","sun"];
        setCheckedDays(preset === "all" ? all : preset === "weekdays" ? weekdays : weekends);
      });
    });

    document.getElementById("wf-delete").addEventListener("click", async function() {
      const idx = parseInt(document.getElementById("wf-index").value);
      if (idx < 0) return;
      const wins = Schedule.getWindows().slice();
      wins.splice(idx, 1);
      try {
        await Schedule.save(wins);
        close();
      } catch (e) {
        showError(e.message);
      }
    });

    document.getElementById("window-form").addEventListener("submit", async function(e) {
      e.preventDefault();
      hideError();
      let w;
      try {
        w = buildWindow();
      } catch (err) {
        showError(err.message);
        return;
      }
      const idx = parseInt(document.getElementById("wf-index").value);
      const wins = Schedule.getWindows().slice();
      if (idx === -1) {
        wins.push(w);
      } else {
        wins[idx] = w;
      }
      try {
        await Schedule.save(wins);
        close();
      } catch (err) {
        showError(err.message);
      }
    });

    document.getElementById("btn-add-window").addEventListener("click", function() { WindowModal.open(-1); });
  }

  return { init, open };
})();

// ------------------------------------------------------------------ //
// System / update                                                     //
// ------------------------------------------------------------------ //

const Updater = (function() {
  let _inited = false;

  const versionEl = () => document.getElementById("sys-version");
  const feedEl    = () => document.getElementById("update-feedback");
  const btn       = () => document.getElementById("btn-check-update");

  async function fetchVersion() {
    try {
      const resp = await apiFetch("/api/version");
      if (!resp.ok) return null;
      const data = await resp.json();
      return data.short || null;
    } catch (e) { return null; }
  }

  function showFeedback(msg, cls) {
    const e = feedEl();
    if (!e) return;
    e.textContent = msg;
    e.className = "feedback " + (cls || "");
    e.classList.remove("hidden");
  }

  async function checkForUpdate() {
    const b = btn();
    b.disabled = true;
    const before = await fetchVersion();
    if (versionEl()) versionEl().textContent = before || "unknown";
    showFeedback("Checking for updates...", "");

    let resp;
    try {
      resp = await apiFetch("/api/update", { method: "POST" });
    } catch (e) {
      showFeedback("Request failed: " + e.message, "err");
      b.disabled = false;
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      showFeedback("Trigger failed: " + (body.error || resp.status), "err");
      b.disabled = false;
      return;
    }

    showFeedback("Update triggered — waiting for restart...", "");
    const startedAt = Date.now();
    const poll = async () => {
      if (Date.now() - startedAt > 90000) {
        showFeedback("Timed out waiting. Check journalctl -u heating-brain-update.", "err");
        b.disabled = false;
        return;
      }
      const now = await fetchVersion();
      if (now && before && now !== before) {
        if (versionEl()) versionEl().textContent = now;
        showFeedback("Updated to " + now + ". Service restarted.", "ok");
        b.disabled = false;
        return;
      }
      if (Date.now() - startedAt > 30000 && now === before) {
        showFeedback("Already up to date (" + before + ").", "");
        b.disabled = false;
        return;
      }
      setTimeout(poll, 4000);
    };
    setTimeout(poll, 4000);
  }

  async function init() {
    if (_inited) return;
    _inited = true;
    const v = await fetchVersion();
    if (versionEl()) versionEl().textContent = v || "unknown";
    const b = btn();
    if (b) b.addEventListener("click", checkForUpdate);
  }

  return { init };
})();

// ------------------------------------------------------------------ //
// Change PIN                                                          //
// ------------------------------------------------------------------ //

const PinChange = (() => {
  function showFeedback(msg, ok) {
    const feedEl = document.getElementById("pin-change-feedback");
    if (!feedEl) return;
    feedEl.textContent = msg;
    feedEl.className = "feedback " + (ok ? "ok" : "err");
    feedEl.classList.remove("hidden");
    setTimeout(() => feedEl.classList.add("hidden"), 3000);
  }

  async function submit() {
    const newPin     = (document.getElementById("pin-new").value     || "").trim();
    const confirmPin = (document.getElementById("pin-confirm").value || "").trim();

    if (!/^\d{4}$/.test(newPin)) {
      showFeedback("PIN must be exactly 4 digits.", false);
      return;
    }
    if (newPin !== confirmPin) {
      showFeedback("PINs do not match.", false);
      return;
    }

    try {
      const resp = await apiFetch("/api/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_pin: newPin }),
      });
      if (resp.ok) {
        showFeedback("PIN changed successfully.", true);
        document.getElementById("pin-new").value = "";
        document.getElementById("pin-confirm").value = "";
      } else {
        const d = await resp.json().catch(() => ({}));
        showFeedback(d.error || "Failed to change PIN.", false);
      }
    } catch (e) {
      if (e.message !== "unauthorized") showFeedback("Network error.", false);
    }
  }

  function init() {
    const btn = document.getElementById("btn-change-pin");
    if (btn) btn.addEventListener("click", submit);
  }

  return { init };
})();

// ------------------------------------------------------------------ //
// Logout                                                              //
// ------------------------------------------------------------------ //

function initLogout() {
  const btn = document.getElementById("btn-logout");
  if (btn) {
    btn.addEventListener("click", async function() {
      await fetch("/api/logout", { method: "POST" });
      showPinScreen();
    });
  }
}

// ------------------------------------------------------------------ //
// App entry point                                                     //
// ------------------------------------------------------------------ //

const App = (function() {
  let started = false;

  function init() {
    if (started) return;
    started = true;

    Router.init();
    Override.init();
    WindowModal.init();
    PinChange.init();
    initLogout();

    // Load schedule once at startup for the NextSchedule widget on Now page
    Schedule.load();

    Status.start();

    // Reload chart every 60s if history page is visible
    setInterval(function() {
      if (Router.currentPage() === "history") {
        Chart.loadBoth();
      }
    }, 60000);
  }

  return { init: init };
})();

// ------------------------------------------------------------------ //
// Boot                                                                //
// ------------------------------------------------------------------ //

document.addEventListener("DOMContentLoaded", async function() {
  PinScreen.init();
  try {
    const resp = await fetch("/api/status");
    if (resp.ok) {
      showApp();
    } else {
      showPinScreen();
    }
  } catch (e) {
    showPinScreen();
  }
});
