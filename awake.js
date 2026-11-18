// Keeps a phone or tablet's screen on while it shows the deck (the Pad's own screen never sleeps anyway).
// The browser's Wake Lock only works on secure pages (https or localhost); a deck on the home network is plain
// http, so a tiny silent video playing in a loop does the same job there. Both start on the first tap.

(() => {
  if (new URLSearchParams(location.search).has("kiosk") || !matchMedia("(pointer: coarse)").matches) return;
  let lock = null, video = null;

  async function keepAwake() {
    if (document.hidden) return;
    if ("wakeLock" in navigator && isSecureContext) {
      try { if (!lock || lock.released) lock = await navigator.wakeLock.request("screen"); return; } catch {}
    }
    if (!video) {
      video = Object.assign(document.createElement("video"), { src: "pwa/awake.mp4", muted: true, loop: true, playsInline: true });
      video.setAttribute("playsinline", "");
      video.setAttribute("muted", "");
      video.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:0;top:0";
      document.body.append(video);
    }
    video.play().catch(() => {});
  }

  addEventListener("pointerdown", keepAwake, { passive: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) keepAwake(); });
})();
