// export-live2d.js
const fs = require("fs");
const path = require("path");

// Direct path to REAL Live2D adapter in your project
const src = path.join(
    __dirname,
    "node_modules",
    "pixi-live2d-display",
    "dist",
    "index.js"
);

// Output location in Electron renderer
const dest = path.join(
    __dirname,
    "electron_app",
    "renderer",
    "libs",
    "pixi-live2d-display.js"
);

// Ensure directory exists
fs.mkdirSync(path.dirname(dest), { recursive: true });

// Copy file
fs.copyFileSync(src, dest);

console.log("SUCCESS: Live2D adapter exported.");
console.log("FROM:", src);
console.log("TO:  ", dest);
