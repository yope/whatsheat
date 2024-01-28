
import { WSComm } from "/html/ws.js";

function sleep(ms) {
	return new Promise(resolve => setTimeout(resolve, ms));
}

class KachelUI {
	constructor() {
		this.ws = new WSComm("ws://"+window.location.host+"/ws", () => {
			this.main_loop().then((r) => {
				console.log("Ended.");
			});
		});
		this.maindiv = document.getElementById("maindiv");
		this.power_enabled = false;
		this.build_ui();
	}

	_btn(id, txt, cls, cb) {
		let ret = document.createElement("button");
		ret.id = id;
		ret.classList.add(cls);
		ret.onclick = cb;
		ret.innerText = txt;
		return ret;
	}

	_div_cls(...cls) {
		let ret = document.createElement("div");
		for (let c of cls) {
			ret.classList.add(c);
		}
		return ret;
	}

	_mkname(text) {
		let words = text.split("_");
		for (let i=0; i < words.length; i++)
			words[i] = words[i].charAt(0).toUpperCase() + words[i].slice(1);
		return words.join(" ");
	}

	_cls_mod_text(name, text, clsmod, add) {
		const div = document.getElementById(`div-${name}`);
		if (null === div)
			return;
		div.innerText = this._mkname(text);
		if (add)
			div.classList.add(clsmod);
		else
			div.classList.remove(clsmod);
	}

	build_ui() {
		let md = this.maindiv;
		let sv = this._div_cls("pane", "status");
		md.appendChild(sv);
		this.status_view = sv;
		let ms = this._div_cls("pane", "status");
		md.appendChild(ms);
		this.status_miner = ms;
		let bools = ["miner_ok", "can_cool", "need_cooling", "want_aux_heat",
			"want_main_heat", "want_cv_heat", "prefer_aux", "enable_power_control",
			"manual_override"];
		for (const b of bools) {
			let d = this._div_cls("status-bool");
			d.id = `div-${b}`;
			ms.appendChild(d);
		}
		let rs = this._div_cls("pane", "relays");
		let bp = this._div_cls("pane", "buttons");
		md.appendChild(rs);
		md.appendChild(bp);
		this.status_relays = rs;
		let relays = ["relay_contactor", "relay_cool", "relay_cv_heat", "relay_fan", "relay_water"];
		for (const r of relays) {
			let d = this._div_cls("status-relay");
			let btn = this._btn(`btn-${r}`, this._mkname(r), "btn-relay", (ev) => {
				this.ws.remote_call("click", [r], {});
			});
			d.id = `div-${r}`;
			rs.appendChild(d);
			bp.appendChild(btn);
		}
		let bdbtn = this._btn("btn-bidir_valve", "Valve", "btn-relay", (ev) => {
				this.ws.remote_call("click", ["bidir_valve", "toggle"], {});
		});
		bp.appendChild(bdbtn);
		let midbtn = this._btn("btn-bidir_middle", "Valve Mid", "btn-relay", (ev) => {
				this.ws.remote_call("click", ["bidir_valve", "middle"], {});
		});
		bp.appendChild(midbtn);
		let nabtn = this._btn("btn-bidir_nudge_aux", "Nudge AUX", "btn-relay", (ev) => {
				this.ws.remote_call("click", ["nudge_valve_aux"], {});
		});
		bp.appendChild(nabtn);
		let nmbtn = this._btn("btn-bidir_nudge_main", "Nudge Main", "btn-relay", (ev) => {
				this.ws.remote_call("click", ["nudge_valve_main"], {});
		});
		bp.appendChild(nmbtn);
		let ovbtn = this._btn("btn-manual_override", "Manual Override", "btn-relay", (ev) => {
				this.ws.remote_call("click", ["manual_override"], {});
		});
		bp.appendChild(ovbtn);
		let epbtn = this._btn("btn-enable_power_control", "Power Enable", "btn-relay", (ev) => {
				this.ws.remote_call("click", ["enable_power_control"], {});
		});
		bp.appendChild(epbtn);
		let bidir = this._div_cls("status-bidir");
		bidir.id = "div-bidir_valve";
		rs.appendChild(bidir);
	}

	async main_loop() {
		while (true) {
			let ret = await this.ws.remote_call("get", [], {});
			this.process_state(ret);
			await sleep(2000);
		}
		return true;
	}

	process_state(obj) {
		let sv = this.status_view;
		sv.innerHTML = "";
		for (const [sensor, sobj] of Object.entries(obj.sensors)) {
			let d = this._div_cls("sensor-value", sobj.online ? "sensor-value-online": "sensor-value-offline");
			d.innerText = `${sobj.name}: ${sobj.state.toFixed(2)}`;
			sv.appendChild(d);
		}
		for (const [attr, val] of Object.entries(obj)) {
			if (typeof val == "boolean")
				this._cls_mod_text(attr, attr, "status-bool-false", !val);
			if (typeof val == "object") {
				let c = val.class;
				if (c === "Relay") {
					let txt = attr + (val.value? ": ON": ": OFF");
					this._cls_mod_text(attr, txt, "status-relay-on", val.value);
				} else if (c === "Bidir") {
					let txt = `${attr}: [${val.status}] ${val.position}`;
					this._cls_mod_text(attr, txt, "status-bidir-on", val.status !== "off");
				}
			}
			if (typeof val == "string") {
				let d = this._div_cls("sensor-value", "sensor-value-online");
				d.innerText = `${attr}: ${val}`;
				sv.appendChild(d);
			}
		}
		// Special bool: If enable_power_control is true, the button has no effect anymore
		// and needs to be hidden
		if (!this.power_enabled && (undefined !== obj.enable_power_control)) {
			if (obj.enable_power_control) {
				this.power_enabled = true;
				const btn = document.getElementById("btn-enable_power_control");
				if (null !== btn) {
					btn.style.display = "none";
				}
			}
		}
	}
}

export { KachelUI };

