// Minimal obs-websocket v5 client: connect, auto-reconnect, requests, events.

class OBS {
  constructor(target) {
    this.configure(target, false);
    this.pending = new Map();
    this.nextId = 1;
    this.retry = 500;
    this.onStatus = () => {};
    this.onEvent = () => {};
    this.onReady = () => {};
  }

  // Point at another OBS (from the Pad's settings); reconnects straight away if `reconnect`.
  configure({ host, port, password }, reconnect = true) {
    this.host = host || "";
    this.url = `ws://${this.host}:${port || 4455}`;
    this.password = password || "";
    if (!reconnect) return;
    this.retry = 500;
    clearTimeout(this.timer);
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
    this.connect();
  }

  connect() {
    if (!this.host) return this.onStatus("unset"); // no OBS computer chosen yet
    this.onStatus("connecting");
    const ws = (this.ws = new WebSocket(this.url));
    ws.onmessage = (m) => this.#handle(JSON.parse(m.data));
    ws.onclose = () => {
      for (const { reject } of this.pending.values()) reject(new Error("disconnected"));
      this.pending.clear();
      this.onStatus("offline");
      this.timer = setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 5000);
    };
  }

  request(requestType, requestData = {}) {
    return new Promise((resolve, reject) => {
      if (this.ws?.readyState !== WebSocket.OPEN) return reject(new Error("not connected"));
      const requestId = String(this.nextId++);
      this.pending.set(requestId, { resolve, reject });
      this.ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
    });
  }

  async #handle({ op, d }) {
    if (op === 0) {
      // Hello → Identify. Subscribe to Scenes (4), Inputs (8) and Outputs (64) events.
      const identify = { rpcVersion: 1, eventSubscriptions: 4 | 8 | 64 };
      if (d.authentication) {
        const secret = await sha256b64(this.password + d.authentication.salt);
        identify.authentication = await sha256b64(secret + d.authentication.challenge);
      }
      this.ws.send(JSON.stringify({ op: 1, d: identify }));
    } else if (op === 2) {
      this.retry = 500;
      this.onStatus("online");
      this.onReady();
    } else if (op === 5) {
      this.onEvent(d.eventType, d.eventData || {});
    } else if (op === 7) {
      const p = this.pending.get(d.requestId);
      if (!p) return;
      this.pending.delete(d.requestId);
      d.requestStatus.result ? p.resolve(d.responseData || {}) : p.reject(new Error(d.requestStatus.comment || d.requestStatus.code));
    }
  }
}

async function sha256b64(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return btoa(String.fromCharCode(...new Uint8Array(buf)));
}
