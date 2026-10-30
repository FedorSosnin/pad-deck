// Fallback OBS WebSocket connection. Normally set on the Pad itself: Menu → OBS (saved in settings.json).
// `?obs=host:port` in the URL overrides both.
window.DECK = {
  obs: { host: "", port: 4455, password: "" },
};
