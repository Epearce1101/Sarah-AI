// Every animation the director can play must exist: listed in the catalog
// and present as a file (unless the local-only assets aren't installed).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "renderer");
const dir = path.join(root, "assets", "vrm", "animations");
const catalog = JSON.parse(fs.readFileSync(path.join(dir, "catalog.json"), "utf8"));
const ids = new Map(catalog.animations.map((a) => [a.id, a.file]));
const haveFiles = fs.readdirSync(dir).some((f) => f.endsWith(".vrma"));

if (haveFiles) {
  for (const [id, file] of ids) assert.ok(fs.existsSync(path.join(dir, file)), `catalog lists ${id} but ${file} is missing`);
}
const director = fs.readFileSync(path.join(root, "scripts", "avatar3d", "director.js"), "utf8");
const used = new Set([...director.matchAll(/"((?:\d+_[A-Za-z][^"]*)|(?:dm_\d+))"/g)].map((m) => m[1]));
for (const id of used) assert.ok(ids.has(id), `director uses animation ${id}, which isn't in the catalog`);

console.log(JSON.stringify({ ok: true, checked: "animation-catalog", clips: ids.size, referenced: used.size }));
