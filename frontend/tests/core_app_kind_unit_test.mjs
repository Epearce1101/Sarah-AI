// Which windows she watches, keeps quiet for, or treats as work.
import assert from "node:assert/strict";
import { classifyApp } from "../renderer/scripts/core/app-kind.js";

const fg = (app, title, fullscreen = false) => ({ found: true, app, title, fullscreen });
assert.equal(classifyApp(fg("chrome.exe", "Lofi beats - YouTube - Google Chrome")), "video");
assert.equal(classifyApp(fg("vlc.exe", "movie.mkv - VLC media player")), "video");
assert.equal(classifyApp(fg("msedge.exe", "Netflix", true)), "video");
assert.equal(classifyApp(fg("eldenring.exe", "ELDEN RING", true)), "focus");
assert.equal(classifyApp(fg("chrome.exe", "Docs", true)), null, "a big browser isn't a game");
assert.equal(classifyApp(fg("Code.exe", "main.js - Sarah - Visual Studio Code")), "work");
assert.equal(classifyApp(fg("notepad.exe", "notes.txt - Notepad")), null);
assert.equal(classifyApp({ found: false }), null);
assert.equal(classifyApp(fg("Code.exe", "max.js - Visual Studio Code")), "work");
assert.equal(classifyApp(fg("msedge.exe", "Disney+ | Bluey")), "video");
assert.equal(classifyApp(fg("chrome.exe", "youtubers list.xlsx")), null);
console.log("app kind ok");
