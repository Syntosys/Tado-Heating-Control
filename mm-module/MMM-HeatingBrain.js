/* MagicMirror² Module: MMM-HeatingBrain
 *
 * Displays status from the heating-brain service running on the same Pi,
 * with interactive On/Off/Auto override buttons that mirror the mobile UI.
 */

Module.register("MMM-HeatingBrain", {
	defaults: {
		brainUrl: "http://localhost:8423",
		updateIntervalSeconds: 30,
		showSparkline: false,
		staleSeconds: 180,
		showControls: true,
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
		this.pending = null;
		this.requestFetch();
		setInterval(() => this.requestFetch(), this.config.updateIntervalSeconds * 1000);
	},

	requestFetch: function () {
		this.sendSocketNotification("FETCH_STATUS", { brainUrl: this.config.brainUrl });
	},

	sendOverride: function (mode) {
		this.pending = mode;
		this.updateDom();
		this.sendSocketNotification("SET_OVERRIDE", {
			brainUrl: this.config.brainUrl,
			mode: mode,
		});
	},

	socketNotificationReceived: function (notification, payload) {
		if (notification === "STATUS_DATA") {
			this.status = payload;
			this.lastFetchError = null;
			if (payload.outdoor_temp_c != null) {
				this.history.push(payload.outdoor_temp_c);
				if (this.history.length > 30) this.history.shift();
			}
			this.updateDom(300);
		} else if (notification === "STATUS_ERROR") {
			this.lastFetchError = payload;
			Log.error("[MMM-HeatingBrain] fetch failed: " + payload);
			this.updateDom(300);
		} else if (notification === "OVERRIDE_RESULT") {
			this.pending = null;
			this.requestFetch();
		} else if (notification === "OVERRIDE_ERROR") {
			this.pending = null;
			this.lastFetchError = "override: " + payload;
			this.updateDom(200);
		}
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

	formatExpiry: function (epochSeconds) {
		if (!epochSeconds) return "";
		const remain = Math.max(0, Math.floor(epochSeconds - Date.now() / 1000));
		if (remain <= 0) return "";
		if (remain < 60) return remain + "s left";
		if (remain < 3600) return Math.floor(remain / 60) + "m left";
		const h = Math.floor(remain / 3600);
		const m = Math.floor((remain % 3600) / 60);
		return m > 0 ? `${h}h ${m}m left` : `${h}h left`;
	},

	activeMode: function (s) {
		if (s.override_active && (s.override_mode === "on" || s.override_mode === "off")) {
			return s.override_mode;
		}
		return "auto";
	},

	makeTempTile: function (label, value, age) {
		const tile = document.createElement("div");
		tile.className = "hb-temp";
		const l = document.createElement("div");
		l.className = "hb-temp-label";
		l.textContent = label;
		const v = document.createElement("div");
		v.className = "hb-temp-value";
		v.textContent = value;
		const a = document.createElement("div");
		a.className = "hb-temp-age dimmed xsmall";
		a.textContent = age;
		tile.appendChild(l);
		tile.appendChild(v);
		tile.appendChild(a);
		return tile;
	},

	makeButton: function (mode, label, active) {
		const btn = document.createElement("button");
		btn.type = "button";
		btn.className = `hb-btn hb-btn-${mode}` + (active ? " hb-btn-active" : " hb-btn-dull");
		if (this.pending === mode) btn.classList.add("hb-btn-pending");
		btn.textContent = label;
		btn.addEventListener("click", (e) => {
			e.preventDefault();
			if (this.pending) return;
			this.sendOverride(mode);
		});
		return btn;
	},

	getDom: function () {
		const wrap = document.createElement("div");
		wrap.className = "heating-brain";

		if (this.lastFetchError && !this.status) {
			const err = document.createElement("div");
			err.className = "hb-error";
			err.textContent = "Brain unreachable: " + this.lastFetchError;
			wrap.appendChild(err);
			return wrap;
		}
		if (!this.status) {
			const loading = document.createElement("div");
			loading.className = "dimmed light small";
			loading.textContent = "Loading…";
			wrap.appendChild(loading);
			return wrap;
		}

		const s = this.status;
		const stale =
			s.last_loop_at == null ||
			Date.now() / 1000 - s.last_loop_at > this.config.staleSeconds;

		const state = (s.commanded_state || "unknown").toLowerCase();
		const stateLabel = state === "on" ? "HEATING ON" : state === "off" ? "HEATING OFF" : "—";
		const pill = document.createElement("div");
		pill.className = `hb-pill hb-pill-${state}${stale ? " hb-stale" : ""}`;
		pill.textContent = stateLabel;
		wrap.appendChild(pill);

		const temps = document.createElement("div");
		temps.className = "hb-temps";
		temps.appendChild(
			this.makeTempTile("Outdoor", this.formatTemp(s.outdoor_temp_c), this.formatAge(s.outdoor_fetched_at))
		);
		temps.appendChild(
			this.makeTempTile(
				"Indoor",
				this.formatTemp(s.indoor_temp_c),
				s.indoor_temp_c != null ? this.formatAge(s.indoor_fetched_at) : "no sensor"
			)
		);
		temps.appendChild(
			this.makeTempTile("Target", this.formatTemp(s.threshold_c), s.active_window_name || "—")
		);
		wrap.appendChild(temps);

		if (this.config.showControls) {
			const active = this.activeMode(s);
			const row = document.createElement("div");
			row.className = "hb-ctrl-row";
			row.appendChild(this.makeButton("on", "On", active === "on"));
			row.appendChild(this.makeButton("off", "Off", active === "off"));
			row.appendChild(this.makeButton("auto", "Auto", active === "auto"));
			wrap.appendChild(row);

			if (s.override_active && s.override_expiry_at) {
				const expiry = this.formatExpiry(s.override_expiry_at);
				if (expiry) {
					const meta = document.createElement("div");
					meta.className = "hb-ctrl-meta dimmed xsmall";
					const mode = s.override_mode ? s.override_mode.toUpperCase() : "";
					meta.textContent = `Override ${mode} · ${expiry}`;
					wrap.appendChild(meta);
				}
			}
		}

		if (s.last_reason) {
			const reason = document.createElement("div");
			reason.className = "hb-reason dimmed small";
			reason.textContent = s.last_reason;
			wrap.appendChild(reason);
		}

		if (s.last_error && s.last_error_at && Date.now() / 1000 - s.last_error_at < 600) {
			const err = document.createElement("div");
			err.className = "hb-error small";
			err.textContent = "⚠ " + s.last_error;
			wrap.appendChild(err);
		}

		return wrap;
	},
});
