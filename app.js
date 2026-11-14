const params = new URLSearchParams(location.search);
const [hostParam, portParam] = (params.get("obs") || "").split(":");
// Which OBS to talk to: ?obs=host:port in the URL, else Menu → OBS (settings.json), else config.js.
function obsTarget() {
  const s = Feedback.settings;
  return {
    host: hostParam || s.obsHost || DECK.obs.host,
    port: portParam || (s.obsHost ? s.obsPort : DECK.obs.port),
    password: params.get("pw") ?? (s.obsHost ? s.obsPassword : DECK.obs.password),
  };
}
const obs = new OBS(obsTarget());

const SWATCHES = ["#2b2d32", "#7a1430", "#1d5e36", "#23439e", "#56308f", "#9c4a16", "#0f6670", "#d8d9dc"];
const $ = (id) => document.getElementById(id);
let grid = $("grid"); // the visible page; a finished swipe swaps in the pre-drawn neighbour as the new one
document.body.classList.toggle("kiosk", params.has("kiosk")); // the Pad: touch only, no pointer

let layout = { columns: 4, rows: 3, pages: [] };
let saved = "";      // last saved layout JSON, for Cancel and change polling
let editing = false;
let page = 0;        // index of the page on screen; remembered per device
try { page = +localStorage.getItem("page") || 0; } catch {}
const buttons = () => layout.pages[page].buttons;
const state = { scene: null, muted: {}, stream: false, rec: false, shown: {} };

// What a key can do. Scene and mute keys keep their original fields ({scene} / {mute}); every other kind is
// stored as {action: {type, ...}}. OBS kinds run straight from this screen; the rest go through the Pad's
// server (api/run), which reaches smart plugs and web addresses itself and the Pad Deck Helper on the computer.
const ACTIONS = [
  ["OBS", [["scene", "Switch scene"], ["mute", "Mute / unmute audio"], ["stream", "Start / stop stream"],
    ["record", "Start / stop recording"], ["source", "Show / hide a source"]]],
  ["On the computer", [["url", "Open a website"], ["app", "Open an app"], ["shortcut", "Run a Shortcut (Mac)"], ["hotkey", "Press keys"]]],
  ["Smart home & web", [["plug", "Smart plug"], ["http", "Web request"]]],
];
const OBS_KINDS = new Set(["scene", "mute", "stream", "record", "source"]);
const kindOf = (b) => (b.scene !== undefined ? "scene" : b.mute !== undefined ? "mute" : b.action?.type || "scene");
const srcKey = (scene, source) => `${scene}\u0000${source}`;

// ---------- Layout persistence ----------

async function loadLayout() {
  const res = await fetch("api/layout");
  const text = JSON.stringify(await res.json());
  if (text === saved) return false;
  saved = text;
  layout = JSON.parse(text);
  normalize();
  return true;
}

async function saveLayout() {
  // Blank pages are never saved (but there's always at least one page).
  const used = layout.pages.filter((p) => !isBlank(p));
  if (used.length !== layout.pages.length) page = Math.max(0, used.indexOf(layout.pages[page]));
  layout.pages = used.length ? used : [layout.pages[0]];
  normalize();
  const body = JSON.stringify(layout);
  const res = await fetch("api/layout", { method: "PUT", headers: { "Content-Type": "application/json" }, body });
  if (!res.ok) throw new Error(res.statusText);
  saved = body;
}

// Pad or trim every page to exactly columns × rows slots.
function normalize() {
  if (!layout.pages?.length) layout.pages = [{ buttons: layout.buttons || [] }];
  delete layout.buttons; // layouts from before pages had a single list
  const size = layout.columns * layout.rows;
  for (const p of layout.pages) {
    p.buttons = p.buttons.slice(0, size);
    while (p.buttons.length < size) p.buttons.push(null);
  }
  page = Math.min(Math.max(page, 0), layout.pages.length - 1);
}

function showPage(i) {
  page = i;
  try { localStorage.setItem("page", i); } catch {}
  render();
}

// ---------- Rendering ----------

function isLight(hex) {
  const n = parseInt((hex || "#2b2d32").slice(1), 16);
  return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 > 0.6;
}

function buttonEl(b) {
  const el = document.createElement("div");
  el.className = "btn" + (isLight(b.bg) ? " light" : "");
  if (b.bg) el.style.setProperty("--bg", b.bg);
  el.append(Object.assign(document.createElement("i"), { className: "led" }));
  let label = b.label;
  if (b.scene && b.scene === state.scene) el.classList.add("live");
  const a = b.action;
  if ((a?.type === "stream" && state.stream) || (a?.type === "record" && state.rec)) el.classList.add("hot");
  if (a?.type === "source" && state.shown[srcKey(a.scene, a.source)]) el.classList.add("live");
  let muted = false;
  if (b.mute) {
    const m = state.muted[b.mute];
    if (m === undefined) el.classList.add("unknown");
    else {
      muted = m;
      el.classList.add(m ? "muted" : "hot");
      label = `${b.label} ${m ? "Muted" : "On"}`;
    }
  }
  if (b.icon) {
    // The icon sits in a wrapper so a "muted" slash can be drawn as part of it.
    const ico = document.createElement("span");
    ico.className = "ico";
    const img = document.createElement("img");
    img.src = `icons/${encodeURIComponent(b.icon)}`;
    ico.append(img);
    if (muted) ico.append(Object.assign(document.createElement("i"), { className: "slash" }));
    el.append(ico);
  }
  const span = document.createElement("span");
  span.textContent = label;
  el.append(span);
  return el;
}

function render() {
  grid.style.gridTemplateColumns = `repeat(${layout.columns}, minmax(0, 1fr))`;
  grid.style.gridTemplateRows = $("rail").style.gridTemplateRows = `repeat(${layout.rows}, minmax(0, 1fr))`;
  renderRail();
  grid.replaceChildren(...slotsFor(buttons()));
  if (!editing && isBlank(layout.pages[page])) grid.append(emptyHint());
  $("cols").textContent = layout.columns;
  $("rows").textContent = layout.rows;
  $("stream").classList.toggle("on", state.stream);
  updateLeds();
  $("rec").classList.toggle("on", state.rec);
  schedulePeeks();
}

// Rebuild the off-screen neighbour pages once things are quiet (after renders, page changes, edits).
function schedulePeeks() {
  clearTimeout(schedulePeeks.t);
  schedulePeeks.t = setTimeout(() => { if (!swipe?.axis && !gesture?.drag) buildPeeks(); }, 600);
}

const emptyHint = () => Object.assign(document.createElement("div"), { className: "empty-hint", textContent: "Empty page · tap ✎ to add buttons" });

function slotsFor(list) {
  return list.map((b, i) => {
    let el;
    if (b) {
      el = buttonEl(b);
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "×";
      el.append(del);
    } else {
      el = document.createElement("div");
      el.className = editing ? "add" : "";
      if (editing) el.textContent = "+";
    }
    el.classList.add("slot");
    el.dataset.i = i;
    return el;
  });
}

function renderRail() {
  $("dots").replaceChildren(...layout.pages.map((p, i) => {
    const d = document.createElement("i");
    d.className = i === page ? "on" : "";
    // The page holding the scene that's live in OBS glows green.
    if (p.buttons.some((b) => b?.scene && b.scene === state.scene)) d.classList.add("has-live");
    return d;
  }));
  $("pgUp").disabled = page === 0;
  $("pgDown").disabled = page === layout.pages.length - 1 && isBlank(layout.pages[page]);
}

// A small yes/no dialog (the kiosk has no native confirm). Like the editor, it ignores touches for a
// moment so the tap that opened it can't land on its buttons.
function confirmDialog(message, okLabel) {
  $("confirmText").textContent = message;
  $("confirmOk").textContent = okLabel;
  $("confirm").classList.add("open", "arming");
  setTimeout(() => $("confirm").classList.remove("arming"), 450);
  return new Promise((resolve) => {
    const done = (ok) => { $("confirm").classList.remove("open"); resolve(ok); };
    $("confirmOk").onclick = () => done(true);
    $("confirmCancel").onclick = () => done(false);
    $("confirm").onclick = (e) => { if (e.target === $("confirm")) done(false); }; // tap outside = cancel
  });
}

function toast(msg) {
  $("toast").textContent = msg;
  $("toast").classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => $("toast").classList.remove("show"), 2400);
}

// ---------- Live mode: pressing buttons ----------

function press(b) {
  if (b.scene) {
    state.scene = b.scene; render(); // optimistic; the OBS event confirms
    obs.request("SetCurrentProgramScene", { sceneName: b.scene }).catch((e) => { toast(e.message); sync(); });
  }
  if (b.mute) obs.request("ToggleInputMute", { inputName: b.mute }).catch((e) => toast(e.message));
  const a = b.action;
  if (!a) return;
  if (a.type === "stream") obs.request("ToggleStream").catch((e) => toast(e.message));
  else if (a.type === "record") obs.request("ToggleRecord").catch((e) => toast(e.message));
  else if (a.type === "source") toggleSource(a).catch((e) => toast(e.message));
  else runAction(a).then((msg) => toast(msg), (e) => toast(e.message));
}

// Non-OBS actions run on the Pad's server. Resolves to a short message for the screen.
async function runAction(a) {
  const res = await fetch("api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a) });
  const body = await res.json().catch(() => ({}));
  if (res.status === 403) throw new Error("Only the Pad itself can run this");
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body.message || "Done";
}

// Scene items are addressed by id; ids are looked up once and remembered (also to read OBS's events).
const itemIds = {}, itemNames = {};
async function sceneItemId(scene, source) {
  const k = srcKey(scene, source);
  if (itemIds[k] === undefined) {
    itemIds[k] = (await obs.request("GetSceneItemId", { sceneName: scene, sourceName: source })).sceneItemId;
    itemNames[srcKey(scene, itemIds[k])] = source;
  }
  return itemIds[k];
}

async function toggleSource(a) {
  const id = await sceneItemId(a.scene, a.source);
  const k = srcKey(a.scene, a.source);
  const on = !(state.shown[k] ?? (await obs.request("GetSceneItemEnabled", { sceneName: a.scene, sceneItemId: id })).sceneItemEnabled);
  state.shown[k] = on; render(); // optimistic; the OBS event confirms
  await obs.request("SetSceneItemEnabled", { sceneName: a.scene, sceneItemId: id, sceneItemEnabled: on });
}

// ---------- Pointer handling: taps (live) and drag/tap (edit) ----------

let gesture = null;

$("stage").addEventListener("pointerdown", (e) => {
  const slot = e.target.closest(".slot");
  if (!slot || slot.parentElement !== grid) return;
  const i = +slot.dataset.i;
  // src remembers the page object (not its index) so the button can be carried across pages mid-drag.
  gesture = { i, slot, src: layout.pages[page], btn: buttons()[i], x: e.clientX, y: e.clientY, onDelete: !!e.target.closest(".del"), drag: null };
  if (!editing && gesture.btn) {
    slot.classList.add("pressed");
    Feedback.tap();
  }
  // Holding any key (or empty slot) for 3 s switches to edit mode; that press then does nothing.
  if (!editing) {
    const g = gesture;
    g.hold = setTimeout(() => {
      g.held = true;
      slot.classList.remove("pressed");
      Feedback.tap();
      fetch("api/buzz", { method: "POST" }).catch(() => {});
      enterEdit();
    }, 3000);
  }
});

addEventListener("pointermove", (e) => {
  if (gesture?.hold && Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) > 12) clearTimeout(gesture.hold);
  if (!gesture || !editing || !gesture.btn || gesture.onDelete || gesture.held) return;
  if (!gesture.drag && Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) < 8) return;
  if (!gesture.drag) {
    const r = gesture.slot.getBoundingClientRect();
    const ghost = gesture.slot.cloneNode(true);
    ghost.id = "ghost";
    Object.assign(ghost.style, { width: `${r.width}px`, height: `${r.height}px`,
      transform: `translate3d(${r.left}px, ${r.top - 6}px, 0) scale(1.05)` });
    document.body.append(ghost);
    gesture.slot.classList.add("dragging");
    gesture.drag = { ghost, dx: gesture.x - r.left, dy: gesture.y - r.top, target: null, flip: 0, frame: 0 };
  }
  const d = gesture.drag;
  d.x = e.clientX; d.y = e.clientY;
  // Once per frame: move the floating key on its own GPU layer (transform only, nothing underneath
  // repaints) and see what it's over. Doing this on every touch event is what made dragging lag.
  d.frame ||= requestAnimationFrame(() => {
    d.frame = 0;
    if (gesture?.drag !== d) return;
    d.ghost.style.transform = `translate3d(${d.x - d.dx}px, ${d.y - d.dy - 6}px, 0) scale(1.05)`;
    retarget();
  });
});

// What's under the dragged button: a slot to drop on, or ▲/▼ to hover over (which flips the page).
function retarget() {
  const d = gesture.drag;
  const under = document.elementFromPoint(d.x, d.y);
  const over = under?.closest(".slot:not(.dragging)") || under?.closest(".pgbtn:not(:disabled)");
  if (over !== d.flipped) d.flipped = null;
  if (d.target === over) return;
  d.target?.classList.remove("drop-target", "flipping");
  clearTimeout(d.flip);
  d.target = over;
  if (!over) return;
  if (over.dataset.dir) {
    // One flip per visit: move off the arrow and back to flip again (no runaway blank pages).
    if (d.flipped === over) return;
    over.classList.add("flipping");
    d.flip = setTimeout(() => flipWhileDragging(+over.dataset.dir, over), 450);
  } else {
    over.classList.add("drop-target");
  }
}

function flipWhileDragging(dir, arrow) {
  if (!gesture?.drag) return;
  gesture.drag.flipped = arrow;
  changePage(dir);
  // Keep the dragged slot dimmed if we're back on its page, then look again (holding still keeps flipping).
  if (layout.pages[page] === gesture.src) grid.children[gesture.i]?.classList.add("dragging");
  gesture.drag.target?.classList.remove("flipping");
  gesture.drag.target = null;
  retarget();
}

addEventListener("pointerup", (e) => endGesture(e, true));
addEventListener("pointercancel", (e) => endGesture(e, false));

function endGesture(e, completed) {
  if (!gesture) return;
  const g = gesture;
  gesture = null;
  clearTimeout(g.hold);
  g.slot.classList.remove("pressed");
  if (g.held || g.swiped) return; // the long press that opened edit mode, or a swipe
  if (!editing && g.btn) Feedback.release();
  if (g.drag) {
    g.drag.ghost.remove();
    clearTimeout(g.drag.flip);
    cancelAnimationFrame(g.drag.frame);
    const t = completed && g.drag.target;
    if (t && !t.dataset.dir) {
      // Swap with the slot it was dropped on, which may be on another page.
      const dest = buttons(), j = +t.dataset.i;
      [g.src.buttons[g.i], dest[j]] = [dest[j], g.src.buttons[g.i]];
    }
    pruneBlankPages(); // also clears pages opened mid-drag and then abandoned
    render();
    return;
  }
  // A tap: only counts if it ended on the slot it started on.
  if (!completed || document.elementFromPoint(e.clientX, e.clientY)?.closest(".slot") !== g.slot) return;
  const b = g.btn;
  if (!editing) { if (b) press(b); return; }
  if (g.onDelete) {
    const list = buttons(), victim = list[g.i];
    confirmDialog(`Delete “${victim.label || "this key"}”?`, "Delete").then((ok) => {
      if (ok && list[g.i] === victim) { list[g.i] = null; render(); }
    });
    return;
  }
  openEditor(g.i);
}

// ---------- Edit mode ----------

function enterEdit() {
  editing = true;
  document.body.classList.add("editing");
  render();
}
$("editBtn").onclick = enterEdit;

// Every other on-screen button (bar, menu, close ✕, editor, dialogs) clicks and buzzes like the keys do.
// Keys and the ▲/▼ rail have their own handling; the on-screen keyboard stays quiet.
document.addEventListener("pointerdown", (e) => {
  const b = e.target.closest("button");
  if (b && !b.disabled && !b.closest("#grid, #rail, #osk")) { b.dataset.fb = "1"; Feedback.tap(); }
});
document.addEventListener("pointerup", (e) => {
  const b = e.target.closest("button[data-fb]");
  if (b) { delete b.dataset.fb; Feedback.release(); }
});
$("menuBtn").onclick = () => Menu.open();

$("cancelEdit").onclick = () => {
  layout = JSON.parse(saved);
  normalize();
  exitEdit();
};

$("doneEdit").onclick = async () => {
  try {
    await saveLayout();
    exitEdit();
    toast("Layout saved");
    sync();
  } catch (e) { toast(`Couldn't save: ${e.message}`); }
};

function exitEdit() {
  // Leaving edit mode on an empty page: drop it and go to the nearest page with buttons.
  if (layout.pages.length > 1 && isBlank(layout.pages[page])) { layout.pages.splice(page, 1); page = Math.max(0, page - 1); }
  pruneBlankPages();
  editing = false;
  document.body.classList.remove("editing");
  render();
}

for (const b of document.querySelectorAll("[data-step]")) {
  b.onclick = () => {
    const [key, d] = b.dataset.step.split(",");
    const next = { ...layout, [key]: Math.min(8, Math.max(1, layout[key] + +d)) };
    // Changing the grid reflows buttons in order; empty slots are dropped first. Keys that still don't fit
    // move on to the next page's free slots (any left over get a new page after it), so shrinking never loses a key.
    const size = next.columns * next.rows;
    const pages = layout.pages.map((p) => ({ ...p, buttons: p.buttons.slice() }));
    let moved = 0;
    for (let i = 0; i < pages.length; i++) {
      const list = pages[i].buttons;
      while (list.length > size && list.includes(null)) list.splice(list.lastIndexOf(null), 1);
      const overflow = list.splice(size).filter(Boolean);
      if (!overflow.length) continue;
      moved += overflow.length;
      const after = pages[i + 1];
      if (after) {
        // Fill the next page's free slots within the new size; anything left starts a page of its own.
        const nextList = after.buttons.slice(0, size);
        while (nextList.length < size) nextList.push(null);
        after.buttons = nextList.concat(after.buttons.slice(size));
        for (let j = 0; j < size && overflow.length; j++) if (!after.buttons[j]) after.buttons[j] = overflow.shift();
      }
      if (overflow.length) pages.splice(i + (after ? 2 : 1), 0, { buttons: overflow }); // after the next page
    }
    layout = { ...next, pages };
    normalize();
    render();
    if (moved) toast(`${moved} key${moved === 1 ? "" : "s"} moved to the next page`);
  };
}

// ---------- Button editor ----------

let editIndex = -1;
let draft = null;
let lists = { scenes: [], inputs: [], icons: [] };

// Opens instantly from cached lists; fresh scene/input/icon lists are fetched in the background.
function openEditor(i) {
  editIndex = i;
  draft = buttons()[i] ? { ...buttons()[i] } : { label: "", bg: SWATCHES[0] };
  $("delBtn").hidden = $("dupBtn").hidden = !buttons()[i];
  if (!draft.scene && !draft.mute && !draft.action) {
    draft.scene = lists.scenes[0] || "";
    draft.label = draft.scene;
  }
  if (draft.action) draft.action = { ...draft.action };
  $("colorPop").hidden = true;
  fillEditor();
  // The tap that opened the editor still has a click to come, which would land on whatever is now under
  // the finger (often the scene dropdown, which then pops open). Ignore touches for a moment.
  $("editor").classList.add("open", "arming");
  setTimeout(() => $("editor").classList.remove("arming"), 450);
  const before = JSON.stringify(lists);
  refreshLists().then(() => {
    if (editIndex !== i || !$("editor").classList.contains("open") || JSON.stringify(lists) === before) return;
    if (!draft.scene && !draft.mute && !draft.action) { draft.scene = lists.scenes[0] || ""; draft.label = draft.scene; }
    fillEditor();
  });
}

async function refreshLists() {
  const [scenes, inputs, icons] = await Promise.all([
    obs.request("GetSceneList").then((r) => r.scenes.map((s) => s.sceneName).reverse()).catch(() => lists.scenes),
    obs.request("GetInputList").then((r) => r.inputs.map((s) => s.inputName)).catch(() => lists.inputs),
    fetch("api/icons").then((r) => r.json()).catch(() => lists.icons),
  ]);
  lists = { scenes, inputs, icons };
}

function options(sel, values, current) {
  const all = current && !values.includes(current) ? [current, ...values] : values;
  sel.replaceChildren(...all.map((v) => new Option(v, v, false, v === current)));
}

// The swatch row and icon grid are built once and only re-marked afterwards: rebuilding 60+ icon
// images on every tap is what made the editor slow on the Pad.
let builtIcons = "";
function buildPickers() {
  if (!$("swatches").children.length) {
    $("swatches").append(...SWATCHES.map((c) => {
      const s = document.createElement("button");
      s.className = "swatch";
      s.dataset.color = c;
      s.style.backgroundColor = c;
      s.onclick = () => { draft.bg = c; markPickers(); updatePreview(); };
      return s;
    }), Object.assign(document.createElement("button"), { id: "colorCustom", title: "Custom color", onclick: openColorPicker }));
  }
  const key = lists.icons.join("\n");
  if (key === builtIcons) return;
  builtIcons = key;
  $("icons").replaceChildren(...[null, ...lists.icons].map((name) => {
    const b = document.createElement("button");
    b.dataset.icon = name || "";
    if (name) b.innerHTML = `<img src="icons/${encodeURIComponent(name)}" loading="lazy" decoding="async" alt="">`;
    else b.textContent = "None";
    b.onclick = () => { draft.icon = name || undefined; markPickers(); updatePreview(); };
    return b;
  }));
}

function markPickers() {
  for (const s of $("swatches").querySelectorAll(".swatch")) s.classList.toggle("on", s.dataset.color === (draft.bg || SWATCHES[0]));
  $("colorCustom").classList.toggle("on", !SWATCHES.includes(draft.bg || SWATCHES[0])); // a custom colour is chosen
  for (const b of $("icons").children) b.classList.toggle("on", b.dataset.icon === (draft.icon || ""));
}

// ---------- Custom colour picker ----------
// Built into the page (Chromium's own picker is a separate popup the on-screen keyboard can't reach):
// drag the square for saturation/brightness, the strip for hue, or type a hex code.

let cp = { h: 0, s: 0, v: 0 };

function hexToHsv(hex) {
  const n = parseInt(hex.slice(1), 16), r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
  const h = !d ? 0 : max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: (h * 60 + 360) % 360, s: max ? d / max : 0, v: max };
}

function hsvToHex({ h, s, v }) {
  const f = (k) => { const x = (k + h / 60) % 6; return v - v * s * Math.max(0, Math.min(x, 4 - x, 1)); };
  return "#" + [f(5), f(3), f(1)].map((c) => Math.round(c * 255).toString(16).padStart(2, "0")).join("");
}

function openColorPicker() {
  cp = hexToHsv(draft.bg || SWATCHES[0]);
  $("colorPop").hidden = false;
  paintColor(true);
}

function paintColor(fromDrag) {
  const hex = hsvToHex(cp);
  $("cpSV").style.setProperty("--hue", `hsl(${cp.h} 100% 50%)`);
  $("cpSV").firstElementChild.style.cssText = `left:${cp.s * 100}%;top:${(1 - cp.v) * 100}%;background:${hex}`;
  $("cpHue").firstElementChild.style.left = `${cp.h / 3.6}%`;
  if (fromDrag) $("cpHex").value = hex.toUpperCase();
  $("cpSwatch").style.background = hex;
  draft.bg = hex;
  markPickers();
  updatePreview();
}

// Drag on an element with pointer capture; `set(fx, fy)` gets the position as 0–1 fractions.
function dragArea(el, set) {
  const at = (e) => {
    const r = el.getBoundingClientRect();
    set(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)));
    paintColor(true);
  };
  el.onpointerdown = (e) => { el.setPointerCapture(e.pointerId); at(e); el.onpointermove = at; };
  el.onpointerup = el.onpointercancel = () => { el.onpointermove = null; };
}
dragArea($("cpSV"), (fx, fy) => { cp.s = fx; cp.v = 1 - fy; });
dragArea($("cpHue"), (fx) => { cp.h = Math.min(359.9, fx * 360); });
$("cpHex").oninput = (e) => {
  const m = e.target.value.trim().match(/^#?([0-9a-f]{6})$/i);
  if (m) { cp = hexToHsv("#" + m[1].toLowerCase()); paintColor(false); }
};
$("cpDone").onclick = () => { $("colorPop").hidden = true; $("cpHex").blur(); };

function fillEditor() {
  const kind = kindOf(draft);
  if (!$("actSel").children.length) {
    $("actSel").append(...ACTIONS.map(([group, kinds]) => {
      const g = document.createElement("optgroup");
      g.label = group;
      g.append(...kinds.map(([k, name]) => new Option(name, k)));
      return g;
    }));
  }
  $("actSel").value = kind;
  fillActionFields(kind);
  $("sceneField").hidden = kind !== "scene";
  $("muteField").hidden = kind !== "mute";
  options($("sceneSel"), lists.scenes, draft.scene);
  options($("muteSel"), lists.inputs, draft.mute);
  $("labelIn").value = draft.label || "";
  buildPickers();
  markPickers();
  updatePreview();
}

function updatePreview() {
  const el = buttonEl(draft);
  el.id = "preview";
  $("preview").replaceWith(el);
}

// A new kind of action starts with a sensible label and icon (kept if you had already chosen your own).
const KIND_DEFAULTS = {
  scene: { label: "", icon: undefined }, mute: { label: "Mic", icon: "deck-mic.svg" },
  stream: { label: "Stream", icon: "deck-broadcast.svg" }, record: { label: "Record", icon: "deck-record.svg" },
  source: { label: "", icon: "deck-eye.svg" }, url: { label: "Website", icon: "deck-screen-website.svg" },
  app: { label: "", icon: "deck-desktop.svg" }, shortcut: { label: "", icon: "deck-lightning.svg" },
  hotkey: { label: "Keys", icon: "deck-keyboard.svg" }, plug: { label: "Plug", icon: "deck-smart-plug.svg" },
  http: { label: "Request", icon: "deck-wifi.svg" },
};
$("actSel").onchange = (e) => {
  const from = kindOf(draft), kind = e.target.value;
  const was = KIND_DEFAULTS[from];
  const autoLabel = !draft.label || draft.label === was.label || draft.label === draft.scene || draft.label === autoName(draft);
  const autoIcon = !draft.icon || draft.icon === was.icon;
  delete draft.scene; delete draft.mute; delete draft.action;
  if (kind === "scene") draft.scene = lists.scenes[0] || "";
  else if (kind === "mute") draft.mute = lists.inputs.find((n) => /mic/i.test(n)) || lists.inputs[0] || "";
  else if (kind === "source") draft.action = { type: kind, scene: state.scene || lists.scenes[0] || "", source: "" };
  else if (kind === "plug") draft.action = { type: kind, brand: "shelly", ip: "", do: "toggle" };
  else if (kind === "http") draft.action = { type: kind, method: "GET", url: "", body: "" };
  else draft.action = { type: kind };
  if (autoLabel) draft.label = kind === "scene" ? draft.scene : KIND_DEFAULTS[kind].label;
  if (autoIcon) draft.icon = KIND_DEFAULTS[kind].icon;
  fillEditor();
};

// The name a key takes from what it does (an app's name, a source's name…), so the label can follow it.
function autoName(b) {
  const a = b.action || {};
  return a.type === "source" ? a.source : a.type === "app" ? a.app : a.type === "shortcut" ? a.shortcut : undefined;
}

// Lists from the Pad Deck Helper (apps, Shortcuts) and OBS (sources in a scene), fetched once when needed.
const helperLists = {};
const sceneSources = {};
function loadHelperList(what) {
  if (helperLists[what]) return;
  helperLists[what] = { loading: true, items: [] };
  fetch(`api/helper?what=${what}`).then((r) => r.json().then((j) => {
    if (!r.ok) throw new Error(j.error || r.statusText);
    helperLists[what] = { items: j[what] || [] };
  })).catch((e) => { helperLists[what] = { items: [], error: e.message }; })
    .finally(() => { if ($("editor").classList.contains("open") && draft.action?.type) fillActionFields(kindOf(draft)); });
}
function loadSources(scene) {
  if (!scene || sceneSources[scene]) return;
  sceneSources[scene] = [];
  obs.request("GetSceneItemList", { sceneName: scene })
    .then((r) => { sceneSources[scene] = r.sceneItems.map((i) => i.sourceName); })
    .catch(() => { delete sceneSources[scene]; })
    .finally(() => { if ($("editor").classList.contains("open") && draft.action?.type === "source") fillActionFields("source"); });
}

// The fields for the chosen kind of action, built fresh each time (there are only a few).
function fillActionFields(kind) {
  const box = $("actFields");
  const a = draft.action;
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
  const hint = (text, err) => el("div", { className: "hint" + (err ? " err" : ""), textContent: text });
  const set = (key, value, follow) => {
    const before = autoName(draft);
    a[key] = value;
    if (follow && (!draft.label || draft.label === before)) { draft.label = autoName(draft) || ""; $("labelIn").value = draft.label; }
    updatePreview();
  };
  const text = (label, key, placeholder, props = {}) => {
    const input = el("input", { className: "input", type: "text", value: a[key] || "", placeholder, autocomplete: "off", spellcheck: false, ...props });
    input.dataset.osk = "";
    input.oninput = () => set(key, input.value);
    return el("label", { className: "field" }, label, input);
  };
  const choose = (label, key, values, { follow, then, placeholder } = {}) => {
    const sel = el("select");
    options(sel, values, a[key]);
    if (placeholder) sel.prepend(new Option(placeholder, "", false, !a[key]));
    if (sel.value !== (a[key] ?? "") && values.length && !placeholder) { if (follow) set(key, sel.value, true); else a[key] = sel.value; }
    sel.onchange = () => { set(key, sel.value, follow); then?.(); };
    return el("label", { className: "field" }, label, sel);
  };
  const pairs = (sel, list) => { // [value, text] options
    sel.replaceChildren(...list.map(([v, t]) => new Option(t, v, false, v === (a[sel.dataset.key] ?? ""))));
    return sel;
  };
  const pick = (label, key, list) => {
    const sel = el("select");
    sel.dataset.key = key;
    pairs(sel, list);
    sel.onchange = () => set(key, sel.value);
    return el("label", { className: "field" }, label, sel);
  };
  // Lists from the Helper: a dropdown once loaded; typing the name works too if the Helper can't be reached yet.
  const fromHelper = (label, key, what) => {
    loadHelperList(what);
    const l = helperLists[what];
    if (l.items.length) return [choose(label, key, l.items, { follow: true, placeholder: `Choose ${what === "apps" ? "an app" : "a Shortcut"}…` })];
    const t = text(label, key, what === "apps" ? "e.g. Spotify" : "Shortcut name");
    t.querySelector("input").oninput = (e) => set(key, e.target.value, true);
    return [t, hint(l.loading ? "Asking the Pad Deck Helper for the list…" : `${l.error || "No list from the Helper"}. Type the name instead.`, !l.loading)];
  };
  const kids = [];
  if (kind === "stream" || kind === "record") {
    kids.push(hint(`Starts ${kind === "stream" ? "streaming" : "recording"} in OBS, or stops it. The key's light is red while it's on.`));
  } else if (kind === "source") {
    loadSources(a.scene);
    kids.push(choose("Scene", "scene", lists.scenes, { then: () => { a.source = ""; fillActionFields(kind); } }),
      choose("Source", "source", sceneSources[a.scene] || [], { follow: true }),
      hint("The key's light is green while the source is showing."));
  } else if (kind === "url") {
    kids.push(text("Web address", "url", "e.g. youtube.com/studio", { inputMode: "url" }));
  } else if (kind === "app") {
    kids.push(...fromHelper("App", "app", "apps"));
  } else if (kind === "shortcut") {
    kids.push(...fromHelper("Shortcut", "shortcut", "shortcuts"));
  } else if (kind === "hotkey") {
    kids.push(text("Keys", "keys", "e.g. cmd+shift+4 or ctrl+f5"),
      hint("Join keys with +: cmd, shift, alt, ctrl, then a letter, number, f1–f12, space, return, esc, tab or an arrow."));
  } else if (kind === "plug") {
    kids.push(pick("Plug type", "brand", [["shelly", "Shelly (Plug S, older models)"], ["shelly2", "Shelly Plus / Gen 2 and newer"], ["tasmota", "Tasmota"]]),
      text("Plug's IP address", "ip", "e.g. 192.168.1.50", { inputMode: "decimal" }),
      pick("When pressed", "do", [["toggle", "Turn on / off"], ["on", "Turn on"], ["off", "Turn off"]]),
      hint("Works with plugs that answer on your network without a cloud account. Find the IP in the plug's app or your router."));
  } else if (kind === "http") {
    kids.push(pick("Method", "method", [["GET", "GET"], ["POST", "POST"], ["PUT", "PUT"]]),
      text("Address", "url", "http://…", { inputMode: "url" }),
      text("Body (optional)", "body", '{"entity_id": "light.desk"}'),
      hint("Sent from the Pad: webhooks, Home Assistant, Companion, any device with a web API."));
  }
  if (!OBS_KINDS.has(kind)) {
    const out = hint("");
    const test = el("button", { className: "barbtn", textContent: "Test" });
    test.onclick = () => {
      out.className = "hint"; out.textContent = "…";
      runAction(draft.action).then((m) => { out.textContent = m; }, (e) => { out.className = "hint err"; out.textContent = e.message; });
    };
    kids.push(el("div", { className: "test-row" }, test, out));
    if (["url", "app", "shortcut", "hotkey"].includes(kind) && !Feedback.settings.helperCode) {
      kids.push(hint("Needs the Pad Deck Helper on your computer: see Menu → Helper.", true));
    }
  }
  box.replaceChildren(...kids);
}

$("sceneSel").onchange = (e) => {
  // Keep the label following the scene name unless it was customised.
  if (!draft.label || draft.label === draft.scene) draft.label = e.target.value;
  draft.scene = e.target.value;
  fillEditor();
};
$("muteSel").onchange = (e) => { draft.mute = e.target.value; updatePreview(); };
$("labelIn").oninput = (e) => { draft.label = e.target.value; updatePreview(); };

$("sheetCancel").onclick = () => $("editor").classList.remove("open");
$("sheetSave").onclick = () => {
  buttons()[editIndex] = draft;
  $("editor").classList.remove("open");
  render();
};
$("delBtn").onclick = () => {
  buttons()[editIndex] = null;
  $("editor").classList.remove("open");
  render();
};
// Duplicate: keeps any edits, then drops a copy in the next free slot (this page first, then later
// pages, else a new page) — drag it wherever it should live, e.g. a Mic key on every page.
$("dupBtn").onclick = () => {
  buttons()[editIndex] = draft;
  $("editor").classList.remove("open");
  const order = [page, ...layout.pages.keys()].filter((p, i, a) => a.indexOf(p) === i);
  let at = order.find((p) => layout.pages[p].buttons.includes(null));
  if (at === undefined) { layout.pages.push({ buttons: [] }); normalize(); at = layout.pages.length - 1; }
  const dest = layout.pages[at].buttons;
  dest[dest.indexOf(null)] = { ...draft };
  if (at !== page) { render(); changePage(at > page ? 1 : -1, at); } else render();
  toast(at === page ? "Copy added · drag it where you want it" : `Copy added on page ${at + 1}`);
};

$("uploadBtn").onclick = () => $("uploadIn").click();
$("uploadIn").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const res = await fetch("api/icons", { method: "POST", headers: { "X-Filename": file.name }, body: file });
  if (!res.ok) return toast("Upload failed");
  draft.icon = (await res.json()).name;
  await refreshLists();
  fillEditor();
};

// ---------- Pages ----------
// Pages stack vertically. ▼ on the last page opens a blank one; blank pages vanish once you leave them
// (and are never saved), so a page only sticks around once it has a button.

const isBlank = (p) => !p.buttons.some(Boolean);

// from: where a swipe left the page (px), so the slide carries on from under the finger.
function changePage(dir, target = page + dir, from = 0) {
  let next = target;
  if (next < 0) return;
  if (next >= layout.pages.length) {
    if (isBlank(layout.pages[page])) return;
    layout.pages.push({ buttons: [] });
    normalize();
  }
  const leaving = grid.cloneNode(true);
  leaving.removeAttribute("id");
  leaving.classList.add("leaving");
  $("stage").append(leaving);
  // Drop the page we're leaving if it's blank (unless it's the only one).
  if (isBlank(layout.pages[page]) && layout.pages.length > 1) {
    layout.pages.splice(page, 1);
    if (next > page) next--;
  }
  showPage(next);
  const h = $("stage").clientHeight;
  const ease = { duration: Math.max(180, 420 * (1 - Math.abs(from) / h)), easing: "cubic-bezier(.33, .9, .3, 1)" };
  leaving.style.transform = "";
  leaving.animate([{ transform: `translateY(${from}px)` }, { transform: `translateY(${-dir * h}px)` }], ease).finished.then(() => leaving.remove());
  grid.animate([{ transform: `translateY(${dir * h + from}px)` }, { transform: "none" }], ease);
}

// ---------- Swiping between pages (live mode) ----------
// Swipe up for the next page, down for the previous one. The page follows the finger with the
// neighbouring page peeking in; let go past a quarter of the screen (or flick) to flip, else it springs back.
// A swipe only starts after 40 px of mostly-vertical movement, so presses with a wobble still count.

let swipe = null;

// Sideways: swipe left pulls the settings in from the right edge. (No swipes in edit mode: touching drags keys.)
$("stage").addEventListener("pointerdown", (e) => {
  if (editing) return;
  swipe = { x0: e.clientX, y0: e.clientY, t: [[e.timeStamp, e.clientY, e.clientX]], axis: null, dx: 0, dy: 0 };
});

addEventListener("pointermove", (e) => {
  if (!swipe) return;
  const dx = e.clientX - swipe.x0, dy = e.clientY - swipe.y0;
  if (!swipe.axis) {
    if (Math.abs(dy) >= 40 && Math.abs(dy) >= Math.abs(dx) * 1.5) { swipe.axis = "y"; startSwipe(); }
    else if (Math.abs(dx) >= 40 && Math.abs(dx) >= Math.abs(dy) * 1.5) { swipe.axis = "x"; startSideSwipe(dx); }
    else return;
  }
  swipe.t.push([e.timeStamp, e.clientY, e.clientX]);
  if (swipe.t.length > 6) swipe.t.shift();
  swipe.dx = dx;
  swipe.dy = dy;
  if (swipe.axis === "y") moveSwipe();
  else moveSideSwipe();
});

// It's a swipe, not a press: cancel the key under the finger (and the long press).
function cancelPress() {
  if (gesture) { clearTimeout(gesture.hold); gesture.slot.classList.remove("pressed"); gesture.swiped = true; }
}

function startSideSwipe(dx) {
  cancelPress();
  swipe.w = innerWidth;
  swipe.side = dx < 0 ? "menu" : null;
  if (swipe.side === "menu") {
    Menu.open();
    $("menu").style.transform = `translateX(${swipe.w}px)`;
  }
}

function moveSideSwipe() {
  if (swipe.side === "menu") $("menu").style.transform = `translateX(${Math.max(0, swipe.w + swipe.dx + 40)}px)`;
}

function endSideSwipe(s, completed) {
  const [[t0, , x0], [t1, , x1]] = [s.t[0], s.t[s.t.length - 1]];
  const v = (x1 - x0) / Math.max(1, t1 - t0); // px/ms; negative = flicking left
  if (s.side === "menu") {
    const x = Math.max(0, s.w + s.dx + 40);
    slideMenu(completed && (s.w - x > s.w * 0.3 || v < -0.5), x);
  }
}

// Glide the settings panel open (to 0) or shut (off the right edge) from wherever the finger left it.
function slideMenu(open, from) {
  const m = $("menu");
  m.style.transform = "";
  const a = m.animate([{ transform: `translateX(${from}px)` }, { transform: `translateX(${open ? 0 : innerWidth}px)` }],
    { duration: 240, easing: "cubic-bezier(.33, .9, .3, 1)", fill: "forwards" });
  a.finished.then(() => { if (!open) Menu.close(); a.cancel(); });
}

// Swipe right on the open settings to push them back out (not when the swipe starts on a control).
let menuSwipe = null;
$("menu").addEventListener("pointerdown", (e) => {
  if (e.target.closest("input, select, textarea, .xterm, .slider")) return;
  menuSwipe = { x0: e.clientX, y0: e.clientY, t: [[e.timeStamp, e.clientX]], active: false, dx: 0 };
});
addEventListener("pointermove", (e) => {
  const s = menuSwipe;
  if (!s) return;
  const dx = e.clientX - s.x0, dy = e.clientY - s.y0;
  if (!s.active) {
    if (Math.abs(dy) > 40) { menuSwipe = null; return; } // scrolling a list
    if (dx < 40 || dx < Math.abs(dy) * 1.5) return;
    s.active = true;
  }
  s.t.push([e.timeStamp, e.clientX]);
  if (s.t.length > 6) s.t.shift();
  s.dx = dx;
  $("menu").style.transform = `translateX(${Math.max(0, dx - 40)}px)`;
});
const endMenuSwipe = (completed) => {
  const s = menuSwipe;
  menuSwipe = null;
  if (!s?.active) return;
  const v = (s.t[s.t.length - 1][1] - s.t[0][1]) / Math.max(1, s.t[s.t.length - 1][0] - s.t[0][0]);
  const x = Math.max(0, s.dx - 40);
  slideMenu(!(completed && (x > innerWidth * 0.3 || v > 0.5)), x);
  // The finger lifting over a menu item shouldn't also tap it.
  const eat = (e) => { e.stopPropagation(); e.preventDefault(); };
  addEventListener("click", eat, { capture: true, once: true });
  setTimeout(() => removeEventListener("click", eat, { capture: true }), 400);
};
addEventListener("pointerup", () => endMenuSwipe(true));
addEventListener("pointercancel", () => endMenuSwipe(false));

function startSwipe() {
  cancelPress();
  swipe.h = $("stage").clientHeight;
  swipe.peeks = takePeeks();
}

// The pages above and below, built (and drawn) ahead of time just off-screen so a swipe starts instantly.
let peekCache = null;
function buildPeeks() {
  for (const p of Object.values(peekCache?.els || {})) p.remove();
  peekCache = null;
  if (editing) return;
  const els = {};
  for (const dir of [1, -1]) {
    const next = page + dir;
    const list = next < 0 ? null : next < layout.pages.length ? layout.pages[next].buttons
      : isBlank(layout.pages[page]) ? null : layout.pages[page].buttons.map(() => null); // a new blank page
    if (!list) continue;
    const peek = document.createElement("div");
    peek.className = "grid leaving";
    peek.style.cssText = grid.style.cssText;
    peek.append(...slotsFor(list), ...(list.some(Boolean) ? [] : [emptyHint()]));
    // A percentage of its own height (= the stage's), so it stays fully off-screen even if the stage resizes.
    peek.style.transform = `translateY(${dir * 100}%)`;
    $("stage").append(peek);
    els[dir] = peek;
  }
  peekCache = { els };
}
function takePeeks() {
  if (!peekCache) buildPeeks();
  const els = peekCache?.els || {};
  peekCache = null; // the swipe owns them now; they're rebuilt after it ends
  return els;
}

// Finish a swipe by promoting the already-drawn neighbour to be the page (no rebuild, no re-draw), then
// let both pages glide the rest of the way from where the finger left them.
function commitSwipe(s, dir, y) {
  const incoming = s.peeks[dir], outgoing = grid;
  for (const [d, peek] of Object.entries(s.peeks)) if (+d !== dir) peek.remove();
  let next = page + dir;
  if (next >= layout.pages.length) { layout.pages.push({ buttons: [] }); normalize(); }
  if (isBlank(layout.pages[page]) && layout.pages.length > 1) {
    layout.pages.splice(page, 1);
    if (next > page) next--;
  }
  outgoing.removeAttribute("id");
  outgoing.classList.add("leaving");
  incoming.id = "grid";
  incoming.classList.remove("leaving");
  grid = incoming;
  page = next;
  try { localStorage.setItem("page", page); } catch {}
  renderRail();
  const ease = { duration: Math.max(160, 400 * (1 - Math.abs(y) / s.h)), easing: "cubic-bezier(.33, .9, .3, 1)" };
  outgoing.style.transform = incoming.style.transform = "";
  outgoing.animate([{ transform: `translateY(${y}px)` }, { transform: `translateY(${-dir * s.h}px)` }], ease).finished.then(() => outgoing.remove());
  grid.animate([{ transform: `translateY(${dir * s.h + y}px)` }, { transform: "none" }], ease);
  schedulePeeks();
}

// Where the page sits for a finger offset: 1:1 if there's a page that way, heavy resistance if not.
function swipeOffset(s, dy) {
  return s.peeks[dy < 0 ? 1 : -1] ? dy : dy * 0.25;
}

function moveSwipe() {
  const y = swipeOffset(swipe, swipe.dy - Math.sign(swipe.dy) * 40);
  grid.style.transform = `translateY(${y}px)`;
  for (const [dir, peek] of Object.entries(swipe.peeks)) peek.style.transform = `translateY(${dir * swipe.h + y}px)`;
}

addEventListener("pointerup", () => endSwipe(true));
addEventListener("pointercancel", () => endSwipe(false));

function endSwipe(completed) {
  const s = swipe;
  swipe = null;
  if (!s?.axis) return;
  if (s.axis === "x") return endSideSwipe(s, completed);
  const y = swipeOffset(s, s.dy - Math.sign(s.dy) * 40);
  const [[t0, y0], [t1, y1]] = [s.t[0], s.t[s.t.length - 1]];
  const v = (y1 - y0) / Math.max(1, t1 - t0); // px/ms; negative = flicking up
  const dir = y < 0 ? 1 : -1;
  const go = completed && s.peeks[dir] && (Math.abs(y) > s.h * 0.25 || Math.abs(v) > 0.5 && Math.sign(v) === Math.sign(y));
  if (go) {
    Feedback.release();
    commitSwipe(s, dir, y);
    return;
  }
  // Spring back.
  const back = { duration: 260, easing: "cubic-bezier(.33, .9, .3, 1)" };
  grid.style.transform = "";
  grid.animate([{ transform: `translateY(${y}px)` }, { transform: "none" }], back);
  for (const [d, peek] of Object.entries(s.peeks)) {
    peek.style.transform = `translateY(${d * s.h}px)`;
    peek.animate([{ transform: `translateY(${d * s.h + y}px)` }, { transform: `translateY(${d * s.h}px)` }], back);
  }
  // Springing back keeps the same neighbours: hand them back to the cache.
  setTimeout(() => { for (const p of Object.values(s.peeks)) p.remove(); if (!swipe && !peekCache) buildPeeks(); }, 300);
}

// Remove blank pages other than the one on screen (there's always at least one page).
function pruneBlankPages() {
  const current = layout.pages[page];
  layout.pages = layout.pages.filter((p) => p === current || !isBlank(p));
  page = layout.pages.indexOf(current);
}

for (const b of document.querySelectorAll(".pgbtn")) {
  b.addEventListener("pointerdown", () => { if (!b.disabled) Feedback.tap(); });
  b.addEventListener("pointerup", () => { if (!b.disabled) Feedback.release(); });
  b.onclick = () => changePage(+b.dataset.dir);
}

// ---------- OBS state ----------

async function sync() {
  try {
    const s = await obs.request("GetCurrentProgramScene");
    state.scene = s.sceneName ?? s.currentProgramSceneName;
    for (const name of new Set(layout.pages.flatMap((p) => p.buttons).filter((b) => b?.mute).map((b) => b.mute))) {
      state.muted[name] = (await obs.request("GetInputMute", { inputName: name })).inputMuted;
    }
    for (const k of Object.keys(itemIds)) delete itemIds[k]; // OBS may have restarted: ids can change
    for (const a of layout.pages.flatMap((p) => p.buttons).filter((b) => b?.action?.type === "source").map((b) => b.action)) {
      const id = await sceneItemId(a.scene, a.source).catch(() => null);
      if (id !== null) state.shown[srcKey(a.scene, a.source)] = (await obs.request("GetSceneItemEnabled", { sceneName: a.scene, sceneItemId: id })).sceneItemEnabled;
    }
    state.stream = (await obs.request("GetStreamStatus")).outputActive;
    state.rec = (await obs.request("GetRecordStatus")).outputActive;
  } catch (e) { console.warn("sync failed", e); }
  render();
}

// The Pad 7's two top LEDs: left = OBS connection, right = on air (red streaming, amber recording only).
// Colours are full-strength [r, g, b], scaled way down: WS2812s are blinding even at a few percent.
const LED = { off: [0, 0, 0], green: [0, 255, 20], amber: [255, 110, 0], red: [255, 0, 0] };
let ledSent = "";
function updateLeds() {
  if (!params.has("kiosk")) return; // only the Pad's own screen drives its LEDs
  const k = Feedback.settings.leds ? 0.0154 : 0; // Menu → System → Status lights
  const hex = (rgb) => "#" + rgb.map((c) => Math.round(c * k).toString(16).padStart(2, "0")).join("");
  const conn = { online: LED.green, connecting: LED.amber, offline: LED.red }[state.conn] || LED.off;
  const air = state.stream ? LED.red : state.rec ? LED.amber : LED.off;
  const body = JSON.stringify({ colors: [hex(conn), hex(air)] });
  if (body === ledSent) return;
  ledSent = body;
  fetch("api/leds", { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch(() => { ledSent = ""; });
}

obs.onStatus = (s) => {
  state.conn = s;
  updateLeds();
  $("conn").className = `chip ${s}`;
  $("connText").textContent = { online: "OBS", connecting: "Connecting", offline: "OBS offline", unset: "Set up OBS" }[s];
};
obs.onReady = () => { sync(); refreshLists(); }; // warm the editor's lists
obs.onEvent = (type, d) => {
  if (type === "CurrentProgramSceneChanged") state.scene = d.sceneName;
  else if (type === "InputMuteStateChanged") state.muted[d.inputName] = d.inputMuted;
  else if (type === "StreamStateChanged") state.stream = d.outputActive;
  else if (type === "RecordStateChanged") state.rec = d.outputActive;
  else if (type === "SceneItemEnableStateChanged" && itemNames[srcKey(d.sceneName, d.sceneItemId)] !== undefined) {
    state.shown[srcKey(d.sceneName, itemNames[srcKey(d.sceneName, d.sceneItemId)])] = d.sceneItemEnabled;
  }
  else return;
  if (!editing) render();
};

// Pick up layout edits made from another device (e.g. editing from the Mac).
setInterval(async () => {
  if (!editing && (await loadLayout().catch(() => false))) { render(); sync(); }
}, 3000);

// Start-up: the chrome badge gleams on bare carbon (first load of the session only). Then the keys rise from
// below as one block and push the badge up and away at exactly their speed, so the two never overlap;
// the top bar drops in once the badge has cleared the top.
async function intro() {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  // The badge's script font isn't requested until something uses it, so ask for it explicitly; otherwise
  // the badge's first frame draws in a fallback font and then "snaps" to the script.
  await Promise.all([document.fonts.load('168px "Pad Script"').catch(() => {}), document.fonts.ready]);
  let firstLoad = true;
  try { firstLoad = !sessionStorage.getItem("introDone"); sessionStorage.setItem("introDone", "1"); } catch {}
  if (firstLoad) {
    $("badge").hidden = false;
    await wait(2600);
  } else {
    await wait(350); // a beat of bare carbon first
  }
  document.body.classList.remove("intro");
  const rise = { duration: 1000, easing: "cubic-bezier(.33, .9, .3, 1)", fill: "backwards" };
  const lift = innerHeight;
  $("main").animate([{ transform: `translateY(${lift}px)` }, { transform: "none" }], rise);
  if (firstLoad) {
    $("badge").animate([{ transform: "none" }, { transform: `translateY(-${lift}px)` }], { ...rise, fill: "forwards" })
      .finished.then(() => { $("badge").hidden = true; });
  }
  $("bar").animate([{ transform: "translateY(-100%)" }, { transform: "none" }],
    { duration: 500, delay: firstLoad ? 550 : 250, easing: "cubic-bezier(.16, 1, .3, 1)", fill: "backwards" });
}

loadLayout().then(() => { render(); sync(); }).catch(() => toast("Couldn't load layout")).finally(intro);
// Connect once the settings (which may name the OBS computer) have loaded. A new Pad with no OBS chosen
// yet opens Menu → OBS so it can be set up on the touchscreen.
Feedback.ready.then(() => {
  obs.configure(obsTarget());
  if (!obs.host && params.has("kiosk")) setTimeout(() => Menu.open("obs"), 3500);
});
$("conn").onclick = () => Menu.open("obs");
