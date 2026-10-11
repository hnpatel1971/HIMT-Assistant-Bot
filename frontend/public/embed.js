/**
 * HIMT AI Chatbot — Embed snippet.
 *
 * Paste this on any page of himtmarine.com (just before </body>):
 *   <script src="https://<your-chatbot-host>/embed.js" async></script>
 *
 * It creates a bottom-right floating iframe that loads the HIMT chatbot.
 * The iframe auto-resizes when the chat panel opens/closes.
 */
(function () {
  if (window.__HIMT_CHATBOT_LOADED__) return;
  window.__HIMT_CHATBOT_LOADED__ = true;

  var SCRIPT = document.currentScript || (function () {
    var s = document.getElementsByTagName("script");
    return s[s.length - 1];
  })();

  // Origin of the chatbot host (auto-detected from this script's src).
  var CHAT_ORIGIN = (function () {
    try { return new URL(SCRIPT.src).origin; } catch (_) { return ""; }
  })();

  var CHAT_URL = CHAT_ORIGIN + "/";

  // Size presets
  var CLOSED_W = 200; // room for pill button
  var CLOSED_H = 88;
  var OPEN_W = 440;
  var OPEN_H = 640;

  function applySize(state) {
    if (state === "open") {
      iframe.style.width = Math.min(OPEN_W, window.innerWidth - 24) + "px";
      iframe.style.height = Math.min(OPEN_H, window.innerHeight - 24) + "px";
    } else {
      iframe.style.width = CLOSED_W + "px";
      iframe.style.height = CLOSED_H + "px";
    }
  }

  var iframe = document.createElement("iframe");
  iframe.id = "himt-chatbot-iframe";
  iframe.title = "HIMT AI Assistant";
  iframe.src = CHAT_URL;
  iframe.allow = "clipboard-write";
  iframe.style.cssText = [
    "position:fixed",
    "right:0",
    "bottom:0",
    "border:0",
    "background:transparent",
    "color-scheme:normal",
    "z-index:2147483000",
    "pointer-events:auto",
    "transition:width .25s ease, height .25s ease",
  ].join(";");

  // Start collapsed (just the trigger button)
  applySize("close");

  // Listen for resize messages from the chatbot page.
  window.addEventListener("message", function (event) {
    if (!event.data || event.data.source !== "himt-chatbot") return;
    if (CHAT_ORIGIN && event.origin && event.origin !== CHAT_ORIGIN) return;
    if (event.data.type === "open") applySize("open");
    else if (event.data.type === "close") applySize("close");
  });

  // Re-apply size on host window resize.
  window.addEventListener("resize", function () {
    var isOpen = parseInt(iframe.style.width, 10) > CLOSED_W + 10;
    applySize(isOpen ? "open" : "close");
  });

  function mount() {
    if (!document.body) {
      return setTimeout(mount, 50);
    }
    document.body.appendChild(iframe);
  }
  mount();
})();
