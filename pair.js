// Which device served this page, and pairing for phones, tablets and computers on the network.
// The Pad's own screen and the computer running the Helper are trusted; other devices send the pairing code
// (kept in this browser) with every API call. A link ending in #code=123456 fills it in. See docs/server-api.md.

const Device = (() => {
  const KEY = "padDeckCode";
  let code = "";
  try { code = localStorage.getItem(KEY) || ""; } catch {}
  const m = location.hash.match(/code=(\d{4,})/);
  if (m) {
    code = m[1];
    try { localStorage.setItem(KEY, code); } catch {}
    history.replaceState(null, "", location.pathname + location.search);
  }

  const realFetch = window.fetch.bind(window);
  const isApi = (url) => /^\/?api\//.test(url) || url.startsWith(location.origin + "/api/");
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (isApi(url) && code) {
      const headers = new Headers(init.headers || {});
      headers.set("X-Pad-Deck-Code", code);
      init = { ...init, headers };
    }
    const res = await realFetch(input, init);
    if (isApi(url) && res.status === 401) res.clone().json().then((j) => { if (j.pair) ask(!!code); }).catch(() => {});
    return res;
  };

  const current = { device: "pad7", local: true, paired: true };
  const info = realFetch("api/device", { headers: code ? { "X-Pad-Deck-Code": code } : {} })
    .then((r) => r.json()).then((d) => Object.assign(current, d)).catch(() => current);
  info.then((d) => {
    document.body.dataset.device = d.device;
    if (!d.paired) ask(!!code);
  });

  let shown = false;
  function ask(wrong) {
    if (shown) return;
    shown = true;
    const helper = current.device === "helper";
    const box = document.createElement("div");
    box.id = "pairBox";
    box.innerHTML = `
      <style>
        #pairBox { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; padding: 16px;
          background: rgba(10,11,13,.92); font-family: var(--font, system-ui); color: #e8e9eb; }
        #pairBox form { width: min(420px, 100%); background: #1c1d21; border-radius: 18px; padding: 26px 24px;
          box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 20px 50px rgba(0,0,0,.6); }
        #pairBox h2 { margin: 0 0 8px; font-size: 24px; }
        #pairBox p { margin: 0 0 18px; color: #a3a6ad; line-height: 1.45; font-size: 15px; }
        #pairBox input { width: 100%; box-sizing: border-box; font: 600 30px/1 var(--font, system-ui); letter-spacing: .25em;
          text-align: center; padding: 14px; border-radius: 12px; border: 2px solid #33353b; background: #101113; color: #fff; }
        #pairBox input:focus { outline: none; border-color: #3fbf6e; }
        #pairBox button { margin-top: 16px; width: 100%; padding: 14px; border: 0; border-radius: 12px; font: 600 18px var(--font, system-ui);
          background: #1f7a45; color: #fff; box-shadow: inset 0 1px 0 rgba(255,255,255,.2), 0 3px 0 #0e3b21; }
        #pairBox .err { color: #ff6b7d; min-height: 1.3em; margin: 10px 0 0; font-size: 15px; }
      </style>
      <form>
        <h2>Enter the pairing code</h2>
        <p>${helper
          ? "It's in the Pad Deck Helper's menu: the menu bar on a Mac, or the tray by the clock on Windows."
          : "It's the Pad Deck Helper's code, the one set on the Pad in Menu → Helper."}</p>
        <input inputmode="numeric" autocomplete="one-time-code" maxlength="8" placeholder="······" required>
        <div class="err">${wrong ? "That code doesn't match. Check it and try again." : ""}</div>
        <button>Connect</button>
      </form>`;
    document.body.append(box);
    const input = box.querySelector("input");
    const err = box.querySelector(".err");
    setTimeout(() => input.focus(), 50);
    box.querySelector("form").onsubmit = async (e) => {
      e.preventDefault();
      const tryCode = input.value.replace(/\D/g, "");
      err.textContent = "Checking…";
      try {
        const d = await realFetch("api/device", { headers: { "X-Pad-Deck-Code": tryCode } }).then((r) => r.json());
        if (!d.paired) { err.textContent = "That code doesn't match. Check it and try again."; return; }
        try { localStorage.setItem(KEY, tryCode); } catch {}
        location.reload();
      } catch { err.textContent = "Can't reach the deck. Check the Wi-Fi and try again."; }
    };
  }

  return { info, current, get code() { return code; } };
})();
