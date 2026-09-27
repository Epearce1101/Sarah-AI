const fs = require("fs");
const path = require("path");
const { app, BrowserWindow } = require("electron");

app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("enable-experimental-web-platform-features");

const ROOT = path.resolve(__dirname, "..");
const ARTIFACTS = path.join(__dirname, "artifacts");

async function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function savePng(win, name) {
  await win.webContents.executeJavaScript("document.body.offsetHeight", true);
  win.webContents.invalidate();
  await wait(150);
  // Hidden Electron windows can return the previous compositor frame on the
  // first capture. Discard one frame so each artifact reflects the current UI.
  await win.capturePage();
  win.webContents.invalidate();
  await wait(250);
  const image = await win.capturePage();
  const target = path.join(ARTIFACTS, name);
  fs.writeFileSync(target, image.toPNG());
  return target;
}

async function main() {
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  await app.whenReady();

  const win = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      preload: path.join(ROOT, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: false,
      backgroundThrottling: false,
    },
  });

  await win.loadFile(path.join(ROOT, "renderer", "index.html"));

  await win.webContents.executeJavaScript(`
    (async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const waitFor = async (predicate, label, timeoutMs = 7000) => {
        const start = performance.now();
        while (performance.now() - start < timeoutMs) {
          if (predicate()) return;
          await wait(50);
        }
        throw new Error("Timed out waiting for " + label);
      };

      await waitFor(() => window.SARAH_UI && window.SARAH_UI.projectModal, "SarahUI");
      await wait(1200);

      const motionReset = document.createElement("style");
      motionReset.id = "a1-snapshot-motion-reset";
      motionReset.textContent = \`
        *, *::before, *::after {
          animation: none !important;
          transition-duration: 0s !important;
          transition-delay: 0s !important;
          scroll-behavior: auto !important;
        }
        .chat-bubble,
        .more-conversation-item,
        .skill-card {
          opacity: 1 !important;
          transform: none !important;
        }
        .more-panel.open {
          opacity: 1 !important;
          transform: translateX(0) !important;
        }
        .project-modal.show {
          display: flex !important;
          opacity: 1 !important;
        }
      \`;
      document.head.appendChild(motionReset);

      const ui = window.SARAH_UI;
      ui.clearChat();
      ui.appendMessage("assistant", "A1 visual QA: tokenized Obsidian Violet shell online.");
      ui.appendMessage("user", "Check desktop, tools panel, modal, and mobile layout.");
      ui.appendMessage("assistant", "Styles are centralized in theme tokens and component classes.");

      const panel = document.getElementById("more-panel");
      panel.classList.add("open");
      ui.moreTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === "skills"));
      ui.moreViewConversations.style.display = "none";
      ui.moreViewMemories.style.display = "none";
      ui.moreViewProjects.style.display = "none";
      ui.moreViewSkills.style.display = "flex";
      ui.renderSkillListOptimized([
        {
          slug: "visual-smoke-skill",
          name: "Visual Smoke Skill",
          description: "A mocked skill row for A1 screenshot QA.",
          enabled: 1,
          stale: false,
          body_len: 128,
          path: "C:\\\\Users\\\\Zero\\\\.openclaw\\\\workspace\\\\skills\\\\visual-smoke-skill\\\\SKILL.md",
        },
      ]);
      await waitFor(() => ui.chatLog.children.length >= 3, "visual chat messages");
      await waitFor(() => panel.classList.contains("open"), "open More panel");
      await waitFor(() => getComputedStyle(panel).opacity === "1", "visible More panel");
    })();
  `, true);

  const desktop = await savePng(win, "a1_desktop_more.png");

  await win.webContents.executeJavaScript(`
    (async () => {
      window.SARAH_UI.projectModal.open("create");
      await new Promise((resolve) => requestAnimationFrame(resolve));
      if (getComputedStyle(document.getElementById("project-modal")).display !== "flex") {
        throw new Error("project modal did not open for visual snapshot");
      }
    })();
  `, true);
  const modal = await savePng(win, "a1_project_modal.png");

  win.setSize(390, 780);
  await win.webContents.executeJavaScript(`
    window.SARAH_UI.projectModal.close();
    document.getElementById("more-panel").classList.remove("open");
  `, true);
  const mobile = await savePng(win, "a1_mobile_chat.png");

  console.log(JSON.stringify({ ok: true, desktop, modal, mobile }, null, 2));
  await win.close();
}

main()
  .catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exitCode = 1;
  })
  .finally(() => app.quit());
