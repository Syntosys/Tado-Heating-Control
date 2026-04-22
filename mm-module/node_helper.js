/* Node helper for MMM-HeatingBrain — does the HTTP work server-side so
 * Electron's CSP doesn't block cross-port requests to the heating brain.
 */

const NodeHelper = require("node_helper");

module.exports = NodeHelper.create({
	start: function () {
		console.log("[MMM-HeatingBrain] node helper started");
	},

	socketNotificationReceived: function (notification, payload) {
		if (notification === "FETCH_STATUS") {
			this.fetchStatus(payload && payload.brainUrl);
		} else if (notification === "SET_OVERRIDE") {
			this.setOverride(payload);
		}
	},

	fetchStatus: function (brainUrl) {
		const url = (brainUrl || "http://localhost:8423") + "/status";
		fetch(url, { cache: "no-store" })
			.then((r) => {
				if (!r.ok) throw new Error("HTTP " + r.status);
				return r.json();
			})
			.then((data) => {
				this.sendSocketNotification("STATUS_DATA", data);
			})
			.catch((err) => {
				this.sendSocketNotification("STATUS_ERROR", err.message || String(err));
			});
	},

	setOverride: function (payload) {
		const brainUrl = (payload && payload.brainUrl) || "http://localhost:8423";
		const mode = payload && payload.mode;
		if (!["on", "off", "auto"].includes(mode)) {
			this.sendSocketNotification("OVERRIDE_ERROR", "invalid mode: " + mode);
			return;
		}
		fetch(brainUrl + "/api/override", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ mode: mode }),
			cache: "no-store",
		})
			.then(async (r) => {
				if (!r.ok) {
					const text = await r.text().catch(() => "");
					throw new Error("HTTP " + r.status + " " + text.slice(0, 120));
				}
				return r.json();
			})
			.then((data) => {
				this.sendSocketNotification("OVERRIDE_RESULT", data);
			})
			.catch((err) => {
				this.sendSocketNotification("OVERRIDE_ERROR", err.message || String(err));
			});
	},
});
