chrome.storage.local.get("connected").then(({ connected }) => {
  const state = document.getElementById("state");
  state.textContent = connected ? "Connected to Sarah" : "Sarah isn't running";
  state.className = "state " + (connected ? "on" : "off");
  document.getElementById("hint").textContent = connected
    ? "She can open tabs, read pages, click and type here. She uses her own tab unless you ask her to use yours."
    : "Start Sarah and this connects by itself within half a minute.";
});
