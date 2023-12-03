/*
 * Websocket Communication
 */

class WSComm {
	constructor(location, connect_cb) {
		this.location = location;
		this.reconnect();
		this.connect_cb = connect_cb;
	}

	reconnect() {
		this.ws_conn = new WebSocket(this.location);
		this.initialized = false;
		this.ws_conn.onopen = this.on_open.bind(this);
		this.ws_conn.onerror = this.on_error.bind(this);
		this.ws_conn.onclose = this.on_close.bind(this);
		this.ws_conn.onmessage = this.on_message.bind(this);
	}

	on_open() {
		this.initialized = true;
		this.sequence = 0;
		this.callqueue = [];
		if (undefined !== this.connect_cb) {
			this.connect_cb();
		}
	}

	on_error() {
		this.initialized = false;
		this.ws_conn.close();
	}

	on_close() {
		this.initialized = false;
		setTimeout(() => {this.reconnect()}, 1000);
	}

	on_message(txt) {
		let obj = JSON.parse(txt.data);
		this.process_obj(obj);
	}

	send_object(obj) {
		let txt = JSON.stringify(obj);
		this.ws_conn.send(txt);
	}

	process_obj(obj) {
		let type = obj.type;
		switch (type) {
		case "response":
			this.handle_response(obj.command, obj.sequence, obj.return);
			break;
		default:
			console.log(`Unknown message type: ${type}`);
			break;
		}
	}

	handle_response(cmd, seq, ret) {
		console.log(`RCV: command: ${cmd}, seq: ${seq}, ret:`, ret);
		this.callqueue[seq](ret);
		this.callqueue[seq] = null;
		if ((seq + 1) == this.sequence) {
			while (this.sequence > 0) {
				if (this.callqueue[this.sequence - 1] === null)
					this.sequence -= 1;
				else
					break;
			}
		}
	}

	async remote_call(command, args, kwargs) {
		let sequence = this.sequence;
		let rp = new Promise(resolve => {
			this.callqueue[this.sequence] = resolve;
			this.sequence += 1;
		});
		this.send_object({command, args, kwargs, sequence});
		return await rp;
	}
}

export {
	WSComm
};
