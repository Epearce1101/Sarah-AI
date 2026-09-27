const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
function assert(condition, message) { if (!condition) throw new Error(message); }
const modelStatus = read("renderer/scripts/model-status.js");
const dashboard = read("renderer/dashboard.js");
const settings = fs.readFileSync(path.resolve(__dirname, "..", "..", "Backend", "backend", "config", "settings.py"), "utf8");

// Frontend fallbacks (shown before the backend answers) must match the
// backend defaults, whatever they are.
const defaultModel = (settings.match(/"SARAH_OPENROUTER_MODEL",\s*"([^"]+)"/) || [])[1];
const defaultContext = (settings.match(/"SARAH_OPENROUTER_CONTEXT_WINDOW_TOKENS",\s*(\d+)/) || [])[1];
assert(defaultModel, "settings.py should declare a default SARAH_OPENROUTER_MODEL");
assert(defaultContext, "settings.py should declare a default SARAH_OPENROUTER_CONTEXT_WINDOW_TOKENS");
assert(settings.includes('openrouter_reasoning_effort'), "OpenRouter reasoning effort setting is missing");
assert(modelStatus.includes(defaultContext), `frontend default online context should match backend (${defaultContext})`);
assert(modelStatus.includes(defaultModel), `frontend default online model should match backend (${defaultModel})`);
assert(dashboard.includes(defaultModel), `dashboard default model should match backend (${defaultModel})`);
assert(!/owl-alpha/.test(modelStatus + dashboard), "retired openrouter/owl-alpha must not be referenced");
assert(modelStatus.includes('OpenRouter -'), "frontend should display the active OpenRouter model name");
assert(modelStatus.includes('Local fallback'), "frontend should hide noisy local model names from the top status label");
assert(!modelStatus.includes('Local fallback ?'), "frontend should not render malformed local fallback separators");
assert(dashboard.includes('_startContextStatusSync'), "dashboard should continuously sync context status");
assert(dashboard.includes('Vision: Ready'), "vision status should not be labeled as the main Ollama LLM");
console.log(JSON.stringify({ ok: true, checked: "model-status-sync" }));
