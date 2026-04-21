/* MagicMirror² Module: MMM-HeatingBrain
 *
 * Displays status from the heating-brain service running on the same Pi.
 * It's a thin view — the brain makes all decisions, this just shows what it's doing.
 */

Module.register("MMM-HeatingBrain", {
	defaults: {
		// Where the heating-brain HTTP API is. If you're running both on the same Pi,
		// localhost is fine. Change to the Pi's LAN IP if MM² runs elsewhere.
		brainUrl: "http://localhost:8423",
		updateIntervalSeconds: 30,
		// If true, show a small history sparkline of the last 30 outdoor readings.
		// (Off by default — keep the mirror simple.)
		showSparkline: false,
		// Fade to grey if the brain hasn't ticked in this many seconds.
		staleSeconds: 180,
	},

	getStyles: function () {
		return ["MMM-HeatingBrain.css"];
	},

	getHeader: function () {
		return "Heating";
	},

	start: function () {
		this.status = null;
		this.lastFetchError = null;
		this.history = [];
		this.scheduleFetch();
	},

	scheduleFetch: function () {
		this.fetchStatus();
		setInterval(() => this.fetchStatus(), this.config.updateIntervalSeconds * 1000);
	},

	fetchStatus: function () {
		fetch(this.config.brainUrl + "/status", { cache: "no-store" })
			.then((r) => {
				if (!r.ok) throw new Error("HTTP " + r.status);
				return r.json();
			})
			.then((data) => {
				this.status = data;
				this.lastFetchError = null;
				if (data.outdoor_temp_c != null) {
					this.history.push(data.outdoor_temp_c);
					if (this.history.length > 30) this.history.shift();
				}
				this.updateDom(300);
			})
			.catch((err) => {
				this.lastFetchError = err.message || String(err);
				this.updateDom(300);
			});
	},

	formatTemp: function (c) {
		if (c == null) return "–";
		return c.toFixed(1) + "°C";
	},

	formatAge: function (epochSeconds) {
		if (!epochSeconds) return "never";
		const age = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
		if (age < 60) return age + "s ago";
		if (age < 3600) return Math.floor(age / 60) + "m ago";
		return Math.floor(age / 3600) + "h ago";
	},

	getDom: function () {
		const wrap = document.createElement("div");
		wrap.className = "heating-brain";

		if (this.lastFetchError && !this.status) {
			wrap.innerHTML = `<div class="hb-error">Brain unreachable: ${this.lastFetchError}</div>`;
			return wrap;
		}
		if (!this.status) {
			wrap.innerHTML = `<div class="dimmed light small">Loading…</div>`;
			return wrap;
		}

		const s = this.status;
		const stale =
			s.last_loop_at == null ||
			Date.now() / 1000 - s.last_loop_at > this.config.staleSeconds;

		// --- Big current-state pill ---
		const state = (s.commanded_state || "unknown").toLowerCase();
		const stateLabel = state === "on" ? "HEATING ON" : state === "off" ? "HEATING OFF" : "—";
		const pill = document.createElement("div");
		pill.className = `hb-pill hb-pill-${state}${stale ? " hb-stale" : ""}`;
		pill.textContent = stateLabel;
		wrap.appendChild(pill);

		// --- Temperature row ---
		const temps = document.createElement("div");
		temps.className = "hb-temps";
		temps.innerHTML = `
			<div class="hb-temp">
				<div class="hb-temp-label">Outdoor</div>
				<div class="hb-temp-value">${this.formatTemp(s.outdoor_temp_c)}</div>
				<div class="hb-temp-age dimmed xsmall">${this.formatAge(s.outdoor_fetched_at)}</div>
			</div>
			<div class="hb-temp">
				<div class="hb-temp-label">Indoor</div>
				<div class="hb-temp-value">${this.formatTemp(s.indoor_temp_c)}</div>
				<div class="hb-temp-age dimmed xsmall">${s.indoor_temp_c != null ? this.formatAge(s.indoor_fetched_at) : "no sensor"}</div>
			</div>
			<div class="hb-temp">
				<div class="hb-temp-label">Threshold</div>
				<div class="hb-temp-value">${this.formatTemp(s.threshold_c)}</div>
				<div class="hb-temp-age dimmed xsmall">${s.active_window_name || "—"}</div>
			</div>
		`;
		wrap.appendChild(temps);

		// --- Last decision reason ---
		if (s.last_reason) {
			const reason = document.createElement("div");
			reason.className = "hb-reason dimmed small";
			reason.textContent = s.last_reason;
			wrap.appendChild(reason);
		}

		// --- Last error (if recent) ---
		if (s.last_error && s.last_error_at && Date.now() / 1000 - s.last_error_at < 600) {
			const err = document.createElement("div");
			err.className = "hb-error small";
			err.textContent = "⚠ " + s.last_error;
			wrap.appendChild(err);
		}

		return wrap;
	},
});
