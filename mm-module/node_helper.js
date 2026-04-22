/* Node helper for MMM-HeatingBrain — does the HTTP fetch on the server
 * side so Electron's CSP doesn't block cross-port requests.
 */

const NodeHelper = require("node_helper");

module.exports = NodeHelper.create({
	start: function () {
		console.log("[MMM-HeatingBrain] node helper started");
	},

	socketNotificationReceived: function (notification, payload) {
		if (notification === "FETCH_STATUS") {
			this.fetchStatus(payload && payload.brainUrl);
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
});
