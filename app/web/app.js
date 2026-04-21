/* ------------------------------------------------------------------ */
/* Heating Brain — mobile web UI                                      */
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
// Screen switching                                                    //
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

  function fmtTemp(v) {
    return (v != null) ? v.toFixed(1) + " °C" : "—";
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function apply(data) {
    const heating = document.getElementById("st-heating");
    const state = (data.commanded_state || "unknown").toLowerCase();
    heating.textContent = state.toUpperCase();
    heating.className = "value badge " + (state === "on" ? "on" : state === "off" ? "off" : "unknown");

    document.getElementById("st-window").textContent = data.active_window_name || "none";
    document.getElementById("st-indoor").textContent = fmtTemp(data.indoor_temp_c);
    document.getElementById("st-outdoor").textContent = fmtTemp(data.outdoor_temp_c);
    document.getElementById("st-reason").textContent = data.last_reason || "—";
    document.getElementById("st-lastcmd").textContent = fmtTime(data.last_tado_command_at);

    const overrideEl = document.getElementById("st-override");
    if (data.override_active && data.override_mode) {
      overrideEl.textContent = data.override_mode.toUpperCase();
      overrideEl.style.color = data.override_mode === "on" ? "var(--on-green)" : "var(--off-red)";
    } else {
      overrideEl.textContent = "none";
      overrideEl.style.color = "";
    }
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

  return { start, stop, poll };
})();

// ------------------------------------------------------------------ //
// Override buttons                                                    //
// ------------------------------------------------------------------ //

const Override = (() => {
  function showFeedback(msg, ok) {
    const feedEl = document.getElementById("override-feedback");
    feedEl.textContent = msg;
    feedEl.className = "feedback " + (ok ? "ok" : "err");
    feedEl.classList.remove("hidden");
    setTimeout(() => feedEl.classList.add("hidden"), 2500);
  }

  async function send(mode) {
    try {
      const resp = await apiFetch("/api/override", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (resp.ok) {
        showFeedback("Override: " + mode, true);
        Status.poll();
      } else {
        const d = await resp.json();
        showFeedback(d.error || "Error", false);
      }
    } catch (e) {
      if (e.message !== "unauthorized") showFeedback("Network error", false);
    }
  }

  function init() {
    document.getElementById("btn-force-on").addEventListener("click", () => send("on"));
    document.getElementById("btn-force-off").addEventListener("click", () => send("off"));
    document.getElementById("btn-resume").addEventListener("click", () => send("auto"));
  }

  return { init };
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

  function render(samples) {
    const svg = document.getElementById("history-chart");
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

    // Heating state background bars.
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

    // Indoor line.
    const indoorPts = samples
      .filter(function(s) { return s.indoor_temp_c != null; })
      .map(function(s)   { return [tx(s.ts), ty(s.indoor_temp_c)]; });
    if (indoorPts.length > 1) svg.appendChild(polyline(indoorPts, "#4f8ef7"));

    // Outdoor line.
    const outdoorPts = samples
      .filter(function(s) { return s.outdoor_temp_c != null; })
      .map(function(s)   { return [tx(s.ts), ty(s.outdoor_temp_c)]; });
    if (outdoorPts.length > 1) svg.appendChild(polyline(outdoorPts, "#f39c12"));
  }

  async function load() {
    try {
      const resp = await apiFetch("/api/history?hours=24");
      if (resp.ok) render(await resp.json());
    } catch (e) {
      if (e.message !== "unauthorized") console.warn("History load error:", e);
    }
  }

  return { load };
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
// Logout                                                              //
// ------------------------------------------------------------------ //

function initLogout() {
  document.getElementById("btn-logout").addEventListener("click", async function() {
    await fetch("/api/logout", { method: "POST" });
    showPinScreen();
  });
}

// ------------------------------------------------------------------ //
// App entry point                                                     //
// ------------------------------------------------------------------ //

const App = (function() {
  let started = false;

  function init() {
    if (started) return;
    started = true;
    Override.init();
    WindowModal.init();
    initLogout();
    Status.start();
    Chart.load();
    Schedule.load();
    setInterval(Chart.load, 60000);
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
