// export-pixi.js
const fs = require("fs");
const path = require("path");

// 1) Resolve the main pixi.js entry (this is usually .../dist/cjs/pixi.js)
const pixiMain = require.resolve("pixi.js");

// 2) Get its directory (.../dist/cjs)
const pixiCjsDir = path.dirname(pixiMain);

// 3) Go up one level to .../dist
const pixiDistDir = path.resolve(pixiCjsDir, "..");

// 4) Build path to dist/browser/pixi.min.js
const src = path.join(pixiDistDir, "browser", "pixi.min.js");

// Destination: electron renderer libs folder
const destDir = path.join(__dirname, "electron_app", "renderer", "libs");
const dest = path.join(destDir, "pixi.min.js");

// Ensure the destination folder exists
fs.mkdirSync(destDir, { recursive: true });

// Copy the file
fs.copyFileSync(src, dest);

console.log("Copied PIXI:");
console.log("  from:", src);
console.log("    to:", dest);
