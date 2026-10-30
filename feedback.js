// Press feedback: a synthesized key click (Web Audio) and a short buzz of the Pad 7's vibration motor.
// Settings live in settings.json on the Pad (Menu → Sound & Feel).

const Feedback = (() => {
  let settings = { sound: true, vibrate: true, volume: 0.6, leds: true };
  let ctx = null;

  const ready = fetch("api/settings").then((r) => r.json()).then((s) => { Object.assign(settings, s); updateLeds(); }).catch(() => {});

  // A plastic key: a dry noise transient through two broad, quickly-damped body resonances, plus a
  // short low thump. No sustained pitch: a ringing tone is what makes a click sound like glass.
  let burst = null;
  function key(level, pitch) {
    ctx ||= new AudioContext();
    if (ctx.state === "suspended") ctx.resume();
    if (!burst) {
      burst = ctx.createBuffer(1, Math.round(ctx.sampleRate * 0.02), ctx.sampleRate);
      const d = burst.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * Math.exp(-i / (ctx.sampleRate * 0.0025));
    }
    const t = ctx.currentTime;
    const vol = level * 0.6 * settings.volume ** 2; // squared: finer control at the quiet end
    const out = ctx.createGain();
    out.gain.value = vol;
    const dull = Object.assign(ctx.createBiquadFilter(), { type: "lowpass" });
    dull.frequency.value = 3500;
    dull.connect(out).connect(ctx.destination);

    const src = ctx.createBufferSource();
    src.buffer = burst;
    for (const [f, q, g] of [[950 * pitch, 2.2, 1.6], [2300 * pitch, 3, 0.9], [5200, 1, 0.25]]) {
      const bp = Object.assign(ctx.createBiquadFilter(), { type: "bandpass" });
      bp.frequency.value = f;
      bp.Q.value = q;
      const g2 = ctx.createGain();
      g2.gain.value = g;
      src.connect(bp).connect(g2).connect(dull);
    }
    src.start(t);

    // The thump of the key bottoming out.
    const thump = Object.assign(ctx.createOscillator(), { type: "sine" });
    thump.frequency.setValueAtTime(190 * pitch, t);
    thump.frequency.exponentialRampToValueAtTime(90, t + 0.02);
    const tg = ctx.createGain();
    tg.gain.setValueAtTime(0.7, t);
    tg.gain.exponentialRampToValueAtTime(0.0001, t + 0.025);
    thump.connect(tg).connect(out);
    thump.start(t);
    thump.stop(t + 0.03);
  }

  // The volume HUD: a bar of 10 segments that pops up whenever the level changes.
  let hud = null, hideTimer = 0, saveTimer = 0;
  function showLevel(level) {
    if (!hud) {
      hud = document.createElement("div");
      hud.id = "volHud";
      hud.innerHTML = `<b>Volume</b><div class="segs">${"<i></i>".repeat(10)}</div><span></span>`;
      document.body.append(hud);
    }
    [...hud.querySelectorAll("i")].forEach((seg, i) => seg.classList.toggle("on", i < level));
    hud.querySelector("span").textContent = level === 0 ? "Off" : level;
    hud.classList.add("show");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => hud.classList.remove("show"), 1600);
  }

  return {
    ready, // resolves once settings.json has loaded
    get settings() { return settings; },
    tap() {
      if (settings.sound) try { key(1, 1); } catch {}
      if (settings.vibrate) fetch("api/buzz", { method: "POST" }).catch(() => {});
    },
    // The quieter, slightly higher click of the key coming back up.
    release() {
      if (settings.sound) try { key(0.35, 1.25); } catch {}
    },
    // Volume in 10 steps (0 = silent, 10 = max); stored as 0–1. Shows the on-screen level and plays a sample click.
    get level() { return Math.round(settings.volume * 10); },
    setVolume(level) {
      level = Math.min(10, Math.max(0, Math.round(level)));
      settings.volume = level / 10;
      showLevel(level);
      dispatchEvent(new CustomEvent("deckvolume", { detail: level }));
      if (settings.sound) try { key(1, 1); } catch {}
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => this.save({}), 400);
    },
    async save(changes) {
      Object.assign(settings, changes);
      await fetch("api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
    },
  };
})();
