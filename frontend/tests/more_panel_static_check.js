const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
function assert(condition, message) { if (!condition) throw new Error(message); }
const styles = read("renderer/styles/styles.css");
const components = read("renderer/styles/a1-components.css");
assert(styles.includes("More Panel Beveled Controls"), "missing More panel layout pass");
assert(components.includes("More panel beveled button pass"), "missing More panel bevel theme pass");
[".more-conversation-item", ".more-conv-rename", ".more-conv-delete", "#more-new-conversation", "#more-refresh-skills"].forEach((selector) => {
  assert(components.includes(selector), `missing beveled selector: ${selector}`);
});
console.log(JSON.stringify({ ok: true, checked: "more-panel-bevel" }));
