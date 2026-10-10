# Changelog

## 1.5.0
- English and Russian interface (menu item `L`, `--lang en|ru`, `SCHUB_LANG`); the choice is saved to `settings.json`.
- New item 20: loading screen — split `loading.sc` into PNG parts + template, rebuild with your own background, game logo and SUPERCELL intro logo (including a smooth diagonal SUPERCELL → your logo transition).
- New item 21: split any `.sc` into PNG parts (cut exactly along the shape polygon) and rebuild after editing.
- Fix: SC2 files with "short" (int16) matrices (e.g. `loading.sc`) are now read and written correctly instead of silently losing the matrices.

## 1.3.x
- Item 19: menu background builder (gradient + drifting symbols + top layer, PNG or GIF), other sizes (e.g. 1848×1112) are fitted with blurred edges.
- Item 2: GIF → SC with any size, like PNG → SC; gifconv is embedded.

## 1.2.0
- ASTC textures for `.sctx`/`.zktx`, clean-up / optimize.
