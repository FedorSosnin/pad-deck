// The ☰ menu: advanced features in sections. Add a feature by adding an entry to SECTIONS.
// System features (Wi-Fi, terminal, power) only work on the Pad itself; the server refuses them from other devices.

const Menu = (() => {
  const root = document.getElementById("menu");
  const icon = (d) => `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICONS = {
    wifi: icon('<path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16.1a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1"/>'),
    terminal: icon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3"/><path d="M13 15h4"/>'),
    sound: icon('<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>'),
    system: icon('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    layouts: icon('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
    lock: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  };

  const SECTIONS = [
    { id: "system", label: "System", icon: ICONS.system, render: renderSystem },
    { id: "layouts", label: "Layouts", icon: ICONS.layouts, render: renderLayouts },
    { id: "wifi", label: "Wi-Fi", icon: ICONS.wifi, render: renderWifi },
    { id: "terminal", label: "Terminal", icon: ICONS.terminal, render: renderTerminal, leave: leaveTerminal },
    { id: "sound", label: "Sound & Feel", icon: ICONS.sound, render: renderSound },
  ];

  let current = null;
  const h = (tag, props = {}, ...kids) => {
    const el = Object.assign(document.createElement(tag), props);
    el.append(...kids.flat().filter((k) => k != null));
    return el;
  };
  const api = async (path, opts = {}) => {
    const res = await fetch(`api/${path}`, { headers: { "Content-Type": "application/json" }, ...opts });
    const body = await res.json().catch(() => ({}));
    if (res.status === 403) throw new Error("Only available on the Pad itself");
    if (!res.ok) throw new Error(body.error || res.statusText);
    return body;
  };
  const button = (label, onclick, cls = "") => h("button", { className: `barbtn ${cls}`, textContent: label, onclick });

  // ---------- Shell ----------

  root.innerHTML = `
    <div class="panel menu-panel">
      <nav class="menu-nav"><div class="menu-title">Menu</div><div class="menu-links"></div></nav>
      <section class="menu-body"><header class="menu-head"><h2></h2><button class="barbtn icon menu-close" title="Close">✕</button></header><div class="menu-content"></div></section>
    </div>`;
  const links = root.querySelector(".menu-links");
  const content = root.querySelector(".menu-content");
  root.querySelector(".menu-close").onclick = close;
  for (const s of SECTIONS) {
    const a = h("button", { className: "menu-link", onclick: () => show(s.id) });
    a.innerHTML = `${s.icon}<span>${s.label}</span>`;
    a.dataset.id = s.id;
    links.append(a);
  }

  function open(id = current?.id || SECTIONS[0].id) {
    root.classList.add("open");
    show(id);
  }

  function close() {
    current?.leave?.();
    current = null;
    root.classList.remove("open");
  }

  function show(id) {
    current?.leave?.();
    current = SECTIONS.find((s) => s.id === id);
    for (const a of links.children) a.classList.toggle("on", a.dataset.id === id);
    root.querySelector(".menu-head h2").textContent = current.label;
    root.querySelector(".menu-panel").dataset.section = id;
    content.replaceChildren();
    current.render(content);
  }

  // ---------- Wi-Fi ----------

  function bars(signal) {
    const n = signal > 75 ? 4 : signal > 50 ? 3 : signal > 25 ? 2 : 1;
    return h("span", { className: "bars" }, ...[1, 2, 3, 4].map((i) => h("i", { className: i <= n ? "on" : "" })));
  }

  async function renderWifi(el) {
    const status = h("div", { className: "card status", textContent: "Checking connection…" });
    const list = h("div", { className: "wifi-list" });
    const scanBtn = button("Scan again", () => load(true));
    el.append(status, h("div", { className: "row spread" }, h("div", { className: "label", textContent: "Networks" }), scanBtn), list);

    async function load(rescan) {
      list.replaceChildren(h("div", { className: "hint", textContent: rescan ? "Scanning…" : "Loading…" }));
      try {
        const w = await api(`wifi${rescan ? "?rescan=1" : ""}`);
        status.replaceChildren(w.current
          ? h("div", {}, h("div", { className: "big", textContent: w.current.ssid }),
              h("div", { className: "hint", textContent: `Connected · ${w.current.ip || "no address"} · signal ${w.current.signal ?? "?"}%` }))
          : h("div", { className: "big", textContent: "Not connected" }));
        list.replaceChildren(...w.networks.map((n) => network(n)));
        if (!w.networks.length) list.append(h("div", { className: "hint", textContent: "No networks found." }));
      } catch (e) {
        status.textContent = e.message;
        list.replaceChildren();
      }
    }

    function network(n) {
      const row = h("div", { className: `net${n.active ? " active" : ""}` });
      const head = h("button", { className: "net-head" },
        bars(n.signal), h("span", { className: "net-name", textContent: n.ssid }),
        n.active ? h("span", { className: "tag", textContent: "Connected" }) : null);
      if (n.secure) head.insertAdjacentHTML("beforeend", `<span class="lock">${ICONS.lock}</span>`);
      head.onclick = () => {
        for (const other of list.querySelectorAll(".net-form")) other.remove();
        if (n.active) return;
        const pw = n.secure ? h("input", { className: "input", type: "password", placeholder: "Password", autocomplete: "off" }) : null;
        pw?.setAttribute("data-osk", "");
        const msg = h("div", { className: "hint" });
        const go = button("Connect", async () => {
          go.disabled = true;
          msg.textContent = `Connecting to ${n.ssid}…`;
          try {
            await api("wifi", { method: "POST", body: JSON.stringify({ ssid: n.ssid, password: pw?.value || "" }) });
            msg.textContent = "Connected.";
            load(false);
          } catch (e) {
            msg.textContent = e.message;
            go.disabled = false;
          }
        }, "primary");
        row.append(h("div", { className: "net-form" }, pw, go, button("Cancel", () => row.querySelector(".net-form").remove()), msg));
        pw?.focus();
      };
      row.append(head);
      return row;
    }

    load(false);
  }

  // ---------- Terminal ----------

  let termSession = null;

  function renderTerminal(el) {
    const host = h("div", { className: "term" });
    const kbdBtn = button("Keyboard", () => {
      document.body.classList.toggle("osk-hidden");
      requestAnimationFrame(() => termSession?.fit.fit());
    });
    el.append(h("div", { className: "row spread term-bar" },
      h("div", { className: "hint", textContent: "Shell on the Pad as user “deck” (sudo allowed)." }), kbdBtn), host);

    const term = new Terminal({
      fontFamily: "DejaVu Sans Mono, Menlo, monospace", fontSize: 15, cursorBlink: true, scrollback: 2000,
      theme: { background: "#0b0c0e", foreground: "#e6e8ec", cursor: "#4dff86", selectionBackground: "#2f5f45" },
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(host);
    const enc = new TextEncoder();
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/term`);
    ws.binaryType = "arraybuffer";
    const send = (s) => ws.readyState === 1 && ws.send(enc.encode(s));
    const sendSize = () => ws.readyState === 1 && ws.send(JSON.stringify({ cols: term.cols, rows: term.rows }));
    ws.onopen = () => { fit.fit(); sendSize(); };
    ws.onmessage = (m) => term.write(new Uint8Array(m.data));
    ws.onclose = (e) => term.write(`\r\n\x1b[90m[session closed${e.code === 1008 ? ": only available on the Pad" : ""}]\x1b[0m\r\n`);
    term.onData(send);
    term.onResize(sendSize);
    OSK.attachTerminal(send);
    const ro = new ResizeObserver(() => fit.fit());
    ro.observe(host);
    termSession = { term, ws, fit, ro };
    if (!OSK.touch) term.focus();
  }

  function leaveTerminal() {
    if (!termSession) return;
    termSession.ro.disconnect();
    termSession.ws.close();
    termSession.term.dispose();
    termSession = null;
    OSK.detachTerminal();
    document.body.classList.remove("osk-hidden");
  }

  // ---------- Sound & Feel ----------

  function renderSound(el) {
    const s = Feedback.settings;
    const toggle = (label, hint, key) => {
      const sw = h("button", { className: `switch${s[key] ? " on" : ""}` }, h("i"));
      sw.onclick = async () => { sw.classList.toggle("on"); await Feedback.save({ [key]: sw.classList.contains("on") }); };
      return h("div", { className: "card setting" }, h("div", {}, h("div", { className: "big", textContent: label }), h("div", { className: "hint", textContent: hint })), sw);
    };
    // 10 notches: 0 is silent, 10 is the loudest.
    const vol = h("input", { type: "range", min: 0, max: 10, step: 1, value: Feedback.level, className: "slider" });
    vol.oninput = () => Feedback.setVolume(+vol.value);
    // Follow changes made elsewhere (e.g. the Pad's volume buttons) while the menu is open.
    const follow = (e) => { if (!vol.isConnected) return removeEventListener("deckvolume", follow); vol.value = e.detail; };
    addEventListener("deckvolume", follow);
    const notched = h("div", { className: "notched" }, vol,
      h("div", { className: "ticks" }, ...Array.from({ length: 11 }, (_, i) => h("span", { textContent: i }))));
    el.append(
      toggle("Click sound", "Plays a key click through the Pad's speaker on every press.", "sound"),
      h("div", { className: "card setting" }, h("div", { className: "big", textContent: "Click volume" }), notched),
      toggle("Vibration", "Short buzz from the Pad's vibration motor on every press.", "vibrate"),
    );
  }

  // ---------- Layouts: save the current key layout under a name, load one back ----------

  async function renderLayouts(el) {
    const name = h("input", { className: "input", type: "text", placeholder: "Name, e.g. Gaming stream", autocomplete: "off" });
    name.dataset.osk = "";
    const msg = h("div", { className: "hint" });
    const list = h("div", { className: "layout-list" });
    const save = button("Save", async () => {
      try {
        await api("layouts", { method: "POST", body: JSON.stringify({ name: name.value.trim() }) });
        msg.textContent = `Saved “${name.value.trim()}”.`;
        name.value = "";
        name.blur();
        refresh();
      } catch (e) { msg.textContent = e.message; }
    }, "primary");
    // Tap once to arm, again within 3 s to do it (loading replaces what's on screen; delete is permanent).
    const confirmed = (label, armed, action, cls = "") => {
      const b = button(label, () => {
        if (!b.dataset.armed) {
          b.dataset.armed = "1";
          b.textContent = armed;
          setTimeout(() => { delete b.dataset.armed; b.textContent = label; }, 3000);
          return;
        }
        action();
      }, cls);
      return b;
    };

    // Start fresh: an empty 4×3 layout, straight into edit mode to add keys.
    const fresh = confirmed("New layout", "Tap to clear the keys", async () => {
      try {
        await api("layouts/new", { method: "POST", body: "{}" });
        await loadLayout();
        page = 0;
        render();
        close();
        enterEdit();
        toast("New layout · the old one is saved as “Before last load”");
      } catch (e) { msg.textContent = e.message; }
    });
    el.append(
      h("div", { className: "card" }, h("div", { className: "big", textContent: "Save current layout" }),
        h("div", { className: "net-form", style: "padding: 10px 0 0" }, name, save, msg)),
      h("div", { className: "card setting", style: "margin-top: 14px" }, h("div", {},
        h("div", { className: "big", textContent: "Start fresh" }),
        h("div", { className: "hint", textContent: "An empty 4 × 3 layout. The current one is kept as “Before last load”." })), fresh),
      list,
    );

    async function refresh() {
      let items;
      try { items = await api("layouts"); } catch (e) { list.replaceChildren(h("div", { className: "hint", textContent: e.message })); return; }
      if (!items.length) {
        list.replaceChildren(h("div", { className: "hint", textContent: "No saved layouts yet." }));
        return;
      }
      list.replaceChildren(...items.map((l) => {
        const when = new Date(l.saved).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
        return h("div", { className: "net layout-row" },
          h("div", { className: "layout-info" },
            h("div", { className: "net-name", textContent: l.name }),
            h("div", { className: "hint", textContent: `${l.columns} × ${l.rows} · ${l.buttons} button${l.buttons === 1 ? "" : "s"} · ${l.pages} page${l.pages === 1 ? "" : "s"} · ${when}` })),
          confirmed("Load", "Tap to replace current", async () => {
            try {
              await api("layouts/load", { method: "POST", body: JSON.stringify({ name: l.name }) });
              await loadLayout();
              page = 0;
              render();
              sync();
              toast(`Loaded “${l.name}”`);
              close();
            } catch (e) { msg.textContent = e.message; }
          }, "primary"),
          confirmed("Delete", "Tap to delete", async () => {
            await fetch(`api/layouts?name=${encodeURIComponent(l.name)}`, { method: "DELETE" });
            refresh();
          }, "danger"));
      }));
    }
    refresh();
  }

  // ---------- System ----------

  async function renderSystem(el) {
    const info = h("div", { className: "card kv", textContent: "Loading…" });
    el.append(info);
    try {
      const s = await api("system");
      info.replaceChildren(...[
        ["Device", s.hostname], ["Address", s.ip || "—"], ["Wi-Fi", s.ssid || "—"],
        ["OBS", `${obs.url.replace("ws://", "")}`], ["Up for", s.uptime], ["Temperature", s.temp ? `${s.temp} °C` : "—"],
      ].map(([k, v]) => h("div", { className: "kv-row" }, h("span", { textContent: k }), h("b", { textContent: v }))));
    } catch (e) { info.textContent = e.message; }

    // The two status LEDs on top of the Pad.
    const leds = h("button", { className: `switch${Feedback.settings.leds ? " on" : ""}` }, h("i"));
    leds.onclick = async () => {
      leds.classList.toggle("on");
      await Feedback.save({ leds: leds.classList.contains("on") });
      updateLeds();
    };
    el.append(h("div", { className: "card setting" }, h("div", {},
      h("div", { className: "big", textContent: "Status lights" }),
      h("div", { className: "hint", textContent: "Left: OBS connected. Right: live." })), leds));

    const confirmed = (label, action, cls) => {
      const b = button(label, async () => {
        if (!b.dataset.armed) {
          b.dataset.armed = "1";
          b.textContent = "Tap again to confirm";
          setTimeout(() => { delete b.dataset.armed; b.textContent = label; }, 3000);
          return;
        }
        try { await api(`system/${action}`, { method: "POST" }); b.textContent = "Working…"; } catch (e) { toast(e.message); }
      }, cls);
      return b;
    };
    el.append(h("div", { className: "row actions" },
      button("Reload screen", () => location.reload()),
      confirmed("Restart Pad", "reboot"),
      confirmed("Shut down", "shutdown", "danger"),
    ));
  }

  return { open, close, sections: SECTIONS };
})();
