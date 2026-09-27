# Frontend libs/

Vendored runtime libraries used by the renderer (PIXI, Live2D Cubism core,
pixi-live2d-display). These are loaded via `<script>` tags from `index.html`.

## Cubism Editor (not vendored)

The Live2D **Cubism Editor** is the desktop authoring tool used to create and
edit `.model3.json` model assets. It is **not** required at runtime — only
the runtime SDK files (`live2dcubismcore.min.js`, `cubism4.min.js`) are.

A copy of the installer (`Live2D_Cubism_Setup_5.2.02.exe`, ~244 MB) used to
live in this directory but was removed to keep the repo lean. If you need
the editor, download the latest version from the official source:

  https://www.live2d.com/en/cubism/download/editor/

The runtime SDK files in this folder are sufficient for SARAH's Live2D
rendering and do **not** need the editor installed.
