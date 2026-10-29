// Built-in on-screen keyboard (the Pad has no system keyboard).
// Text mode: appears for inputs with [data-osk] on touch devices or with ?kiosk.
// Terminal mode: OSK.attachTerminal(send) docks it with Esc/Tab/Ctrl/arrows and sends raw keys.

const OSK = (() => {
  const touch = matchMedia("(pointer: coarse)").matches || new URLSearchParams(location.search).has("kiosk");

  const chars = (s) => [...s];
  const LAYERS = {
    abc: [
      chars("1234567890"),
      chars("qwertyuiop"),
      chars("asdfghjkl'"),
      ["shift", ...chars("zxcvbnm"), "-", "back"],
      ["sym", ",", "space", ".", "enter"],
    ],
    sym: [
      chars("1234567890"),
      chars("!@#$%^&*()"),
      chars("~`|\\/_=+[]"),
      ["shift", ...chars("{}<>;:\"?"), "back"],
      ["sym", ",", "space", ".", "enter"],
    ],
  };
  const TERM_ROW = ["esc", "tab", "ctrl", "left", "up", "down", "right"];
  const LABELS = { shift: "⇧", back: "⌫", space: "space", esc: "Esc", tab: "Tab", ctrl: "Ctrl",
    left: "←", up: "↑", down: "↓", right: "→" };
  const TERM_KEYS = { esc: "\x1b", tab: "\t", left: "\x1b[D", up: "\x1b[A", down: "\x1b[B", right: "\x1b[C",
    back: "\x7f", enter: "\r", space: " " };

  const kbd = document.createElement("div");
  kbd.id = "osk";
  document.body.append(kbd);

  let input = null;     // focused text field (text mode)
  let term = null;      // send function (terminal mode)
  let layer = "abc";
  let shift = false;
  let ctrl = false;

  function draw() {
    const rows = (term ? [TERM_ROW] : []).concat(LAYERS[layer]);
    kbd.replaceChildren(...rows.map((row) => {
      const r = document.createElement("div");
      r.className = "osk-row";
      for (const k of row) {
        const b = document.createElement("button");
        b.dataset.k = k;
        const special = k.length > 1;
        b.className = `osk-key ${special ? `osk-${k}` : "osk-char"}`;
        if ((k === "shift" && shift) || (k === "ctrl" && ctrl)) b.classList.add("on");
        b.textContent = k === "sym" ? (layer === "abc" ? "?123" : "ABC")
          : k === "enter" ? (term ? "Enter ↵" : "Done")
          : LABELS[k] || (shift ? k.toUpperCase() : k);
        r.append(b);
      }
      return r;
    }));
  }

  function typeText(k) {
    const { selectionStart: s, selectionEnd: end } = input;
    if (k === "back") {
      if (s === end && s > 0) input.setRangeText("", s - 1, s, "end");
      else input.setRangeText("", s, end, "end");
    } else {
      input.setRangeText(k === "space" ? " " : k, s, end, "end");
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function typeTerm(k) {
    if (TERM_KEYS[k]) return term(TERM_KEYS[k]);
    if (ctrl && k.length === 1) {
      const code = k.toUpperCase().charCodeAt(0);
      ctrl = false;
      draw();
      if (code >= 64 && code <= 95) return term(String.fromCharCode(code - 64));
    }
    term(k);
  }

  // pointerdown + preventDefault keeps focus (and the caret) in the text field.
  kbd.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const b = e.target.closest(".osk-key");
    if (!b || (!input && !term)) return;
    b.classList.add("down");
    setTimeout(() => b.classList.remove("down"), 90);
    if (typeof Feedback !== "undefined" && Feedback.settings.sound) Feedback.tap();
    let k = b.dataset.k;
    if (k === "shift") { shift = !shift; return draw(); }
    if (k === "ctrl") { ctrl = !ctrl; return draw(); }
    if (k === "sym") { layer = layer === "abc" ? "sym" : "abc"; return draw(); }
    if (k === "enter" && !term) return input.blur();
    if (k.length === 1 && shift) { k = k.toUpperCase(); shift = false; draw(); }
    term ? typeTerm(k) : typeText(k);
  });

  function show() { document.body.classList.add("osk-open"); }
  function hide() { document.body.classList.remove("osk-open"); }

  if (touch) {
    document.addEventListener("focusin", (e) => {
      if (term || !e.target.matches?.("input[data-osk]")) return;
      input = e.target;
      input.setAttribute("inputmode", "none");
      shift = input.value === "" && input.type !== "password";
      layer = "abc";
      draw();
      show();
      requestAnimationFrame(() => input.scrollIntoView({ block: "center" }));
    });
    document.addEventListener("focusout", (e) => {
      if (e.target !== input) return;
      input = null;
      if (!term) hide();
    });
  }

  return {
    touch,
    attachTerminal(send) {
      if (!touch) return;
      term = send; input = null; layer = "abc"; shift = false; ctrl = false;
      draw();
      show();
      document.body.classList.add("osk-term");
    },
    detachTerminal() {
      term = null;
      hide();
      document.body.classList.remove("osk-term");
    },
  };
})();
