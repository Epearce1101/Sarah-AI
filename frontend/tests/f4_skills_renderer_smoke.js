const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { app, BrowserWindow } = require("electron");

app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("enable-experimental-web-platform-features");

const ROOT = path.resolve(__dirname, "..");
const SKILL_SLUG = "ui-smoke-skill";
const SKILL_DIR = path.join(os.homedir(), ".openclaw", "workspace", "skills", SKILL_SLUG);
const SKILL_MD = path.join(SKILL_DIR, "SKILL.md");
const API_PORT = Number(process.env.SARAH_PY_PORT || 8907);
let smokeSkillEnabled = false;

function ensureSmokeSkill() {
  fs.mkdirSync(SKILL_DIR, { recursive: true });
  fs.writeFileSync(
    SKILL_MD,
    [
      "---",
      "name: UI Smoke Skill",
      "slug: ui-smoke-skill",
      "description: Renderer smoke test skill for the SARAH skills UI.",
      "enabled_default: false",
      "---",
      "Use this marker only for frontend validation: [F4-UI-SMOKE].",
      "",
    ].join("\n"),
    "utf8"
  );
}

function readSmokeSkill() {
  const body = fs.readFileSync(SKILL_MD, "utf8");
  return {
    slug: SKILL_SLUG,
    name: "UI Smoke Skill",
    description: "Renderer smoke test skill for the SARAH skills UI.",
    path: SKILL_MD,
    enabled: smokeSkillEnabled ? 1 : 0,
    stale: false,
    body_len: body.length,
    body,
  };
}

function sendJson(res, status, payload) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  });
  res.end(JSON.stringify(payload));
}

function startSkillApiStub() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${API_PORT}`);
    const parts = url.pathname.split("/").filter(Boolean);

    if (req.method === "OPTIONS") {
      sendJson(res, 204, {});
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/skills") {
      sendJson(res, 200, { ok: true, skills: [readSmokeSkill()] });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/skills/discover") {
      sendJson(res, 200, { ok: true, count: 1, fresh: 1, stale: 0 });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/skills/register") {
      sendJson(res, 200, { ok: true, skill: readSmokeSkill() });
      return;
    }

    if (parts.length === 4 && parts[0] === "api" && parts[1] === "skills") {
      const slug = decodeURIComponent(parts[2]);
      const action = parts[3];
      if (slug !== SKILL_SLUG) {
        sendJson(res, 404, { ok: false, error: "unknown skill" });
        return;
      }

      if (req.method === "POST" && (action === "enable" || action === "disable")) {
        smokeSkillEnabled = action === "enable";
        sendJson(res, 200, { ok: true, skill: readSmokeSkill() });
        return;
      }

      if (req.method === "GET" && action === "manifest") {
        sendJson(res, 200, { ok: true, skill: readSmokeSkill() });
        return;
      }
    }

    sendJson(res, 404, { ok: false, error: "not found" });
  });

  return new Promise((resolve, reject) => {
    server.once("error", (err) => {
      if (err && err.code === "EADDRINUSE") {
        console.log(`[Smoke] API port ${API_PORT} already in use; using existing backend.`);
        resolve(null);
        return;
      }
      reject(err);
    });

    server.listen(API_PORT, "127.0.0.1", () => {
      console.log(`[Smoke] Skills API stub listening on ${API_PORT}`);
      resolve(server);
    });
  });
}

async function main() {
  let apiStub = null;
  ensureSmokeSkill();
  apiStub = await startSkillApiStub();

  try {
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

    win.webContents.on("console-message", (_event, _level, message) => {
      if (/^\[(Skills|Smoke|PERF)/.test(message)) {
        console.log(`[renderer] ${message}`);
      }
    });

    await win.loadFile(path.join(ROOT, "renderer", "index.html"));

    const result = await win.webContents.executeJavaScript(`
    (async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const waitFor = async (predicate, label, timeoutMs = 10000) => {
        const start = performance.now();
        while (performance.now() - start < timeoutMs) {
          if (predicate()) return;
          await wait(50);
        }
        throw new Error("Timed out waiting for " + label);
      };
      const assert = (condition, message) => {
        if (!condition) throw new Error(message);
      };

      await waitFor(() => window.SARAH_UI && window.SARAH_UI.backend, "SarahUI");
      const ui = window.SARAH_UI;
      const slug = "${SKILL_SLUG}";

      window.confirm = () => true;
      ui.openMorePanel("skills");

      await ui.refreshSkills({ discover: true });
      await waitFor(() => ui._skillBySlug && ui._skillBySlug.has(slug), "smoke skill row");

      let skill = ui._skillBySlug.get(slug);
      assert(skill, "smoke skill missing after discover");
      assert(!skill.stale, "smoke skill should be fresh");
      assert((skill.body_len || 0) > 0, "smoke skill body length missing");

      await ui.backend.setSkillEnabled(slug, false);
      await ui.refreshSkills();
      await waitFor(() => {
        const s = ui._skillBySlug.get(slug);
        return s && !(s.enabled === true || s.enabled === 1);
      }, "skill disabled baseline");

      const manifestBtn = document.querySelector('[data-slug="' + slug + '"][data-skill-action="manifest"]');
      assert(manifestBtn, "manifest button missing");
      manifestBtn.click();
      await waitFor(() => {
        const detail = document.getElementById("more-skill-detail");
        return detail && detail.textContent.includes("[F4-UI-SMOKE]");
      }, "manifest detail marker");

      const enableBtn = document.querySelector('[data-slug="' + slug + '"][data-skill-action="enable"]');
      assert(enableBtn, "enable button missing");
      enableBtn.click();
      await waitFor(() => {
        const s = ui._skillBySlug.get(slug);
        return s && (s.enabled === true || s.enabled === 1);
      }, "skill enabled from UI");

      const disableBtn = document.querySelector('[data-slug="' + slug + '"][data-skill-action="disable"]');
      assert(disableBtn, "disable button missing");
      disableBtn.click();
      await waitFor(() => {
        const s = ui._skillBySlug.get(slug);
        return s && !(s.enabled === true || s.enabled === 1);
      }, "skill disabled from UI");

      skill = ui._skillBySlug.get(slug);
      console.log("[Smoke] F4 skills UI smoke passed");
      return {
        skillName: skill.name,
        enabled: skill.enabled,
        stale: skill.stale,
        bodyLen: skill.body_len,
      };
    })();
    `, true);

    console.log(JSON.stringify({ ok: true, ...result }, null, 2));
    await win.close();
  } finally {
    if (apiStub) {
      await new Promise((resolve) => apiStub.close(resolve));
    }
  }
}

main()
  .catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exitCode = 1;
  })
  .finally(() => app.quit());
