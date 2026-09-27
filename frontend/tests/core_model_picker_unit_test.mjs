// Unit test for the model picker's filtering (plain Node).
import assert from "node:assert/strict";
import { filterModels } from "../renderer/scripts/core/model-picker.js";

const models = [
  { id: "nvidia/nemotron-3-ultra-550b-a55b:free", name: "NVIDIA: Nemotron 3 Ultra (free)", free: true },
  { id: "anthropic/claude-sonnet-5", name: "Anthropic: Claude Sonnet 5", free: false },
  { id: "poolside/laguna-s-2.1:free", name: "Poolside: Laguna S 2.1 (free)", free: true },
];

assert.deepEqual(filterModels(models, "", true).map((m) => m.id), [
  "nvidia/nemotron-3-ultra-550b-a55b:free",
  "poolside/laguna-s-2.1:free",
]);
assert.deepEqual(filterModels(models, "claude", true), [], "free-only hides paid models");
assert.equal(filterModels(models, "claude sonnet", false)[0].id, "anthropic/claude-sonnet-5");
assert.equal(filterModels(models, "NEMOTRON ultra", false).length, 1, "case-insensitive, all terms");
assert.equal(filterModels(models, "nvidia sonnet", false).length, 0);

console.log(JSON.stringify({ ok: true, checked: "model-picker-filter" }));
