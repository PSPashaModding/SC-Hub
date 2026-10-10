# SC Hub

**English** | [Русский](README.ru.md)

A single-file console toolbox for **Supercell `.sc` assets** (SC1 and SC2): convert PNG/GIF into `.sc`, inspect and edit
existing files, rebuild atlases, merge and clean files, build animated menu backgrounds and custom loading screens.
It is made for client-side modding (for example Brawl Stars / Null's Brawl style mods).

- One file: `sc_hub.py`. No installation besides Python and a few pip packages (installed automatically).
- Menu-driven: pick a number, answer questions, **press Enter to accept the default in `[brackets]`**.
- Interface in **English and Russian** (switch with `L` in the menu, `--lang en|ru`, or `SCHUB_LANG`).
- Windows / Linux / macOS.

> **Disclaimer.** SC Hub is an unofficial community tool, not affiliated with or endorsed by Supercell. It does not
> contain any game assets. You are responsible for the files you process and for complying with the game's terms.
> Results were verified by decoding and rendering the produced files; behaviour inside the game can differ — always keep
> backups of your original files.

## Quick start

1. Install **Python 3.9+** (on Windows tick "Add Python to PATH").
2. Download this repository (or just `sc_hub.py`).
3. Run:
   - Windows: double-click `start.bat`
   - Linux / macOS: `./start.sh`
   - or: `python sc_hub.py` (`python sc_hub.py --lang en` to force English)
4. On first start the missing libraries (`pillow`, `flatbuffers`, `zstandard`, optionally `numpy`) are installed
   automatically. To do it manually: `pip install -r requirements.txt`.

Folders next to the script are created automatically:

| Folder | Purpose |
|---|---|
| `input/`  | put your source files here (`input.png`, `input.gif`, `input.sc`, …). Names typed without a path are looked up here, then in `output/`, then next to the script. |
| `output/` | results are saved here (`output.sc`, dumps, previews). Menu item 11 opens it. |

Every file question also accepts a full path, and `?` opens a file picker window.

## Menu overview

| # | Item | What it does |
|---|---|---|
| 1 | PNG → SC | image (or a whole folder of images) into `.sc` with a clip scale |
| 2 | GIF → SC | animation into `.sc`, any size, transparency |
| 3 | Inspect .sc | summary + detailed dump to `.txt` |
| 4 | Rename exports | change ExportNames |
| 5 | Size and position | scale / offset MovieClips and Shapes |
| 6 | MovieClip FPS | change frame rates |
| 7 | JSON/msgpack + PNG ⇄ .sc | decode a file to editable data + PNG atlases and build it back |
| 8 | Edit TextFields | texts, font size, frame |
| 9 | Rebuild atlases | repack from scratch, drop unused pixels |
| 10 | Delete exports | with the clips and shapes only they use |
| 11 | Output folder | open it in the file explorer |
| 12 | Info by ID | object, parents, ExportName, text/localization |
| 13 | SC package | find `_tex.sc` and `main_<int>.sctx/.zktx` |
| 14 | Merge | many `.sc` into one SC2, or one package into one file |
| 15 | Text table (CSV) | export names/texts and apply them back |
| 16 | Transfer exports | move to a new `.sc` or add to another |
| 17 | File diagnostics | metadata-only report for bug reports |
| 18 | Clean up / optimize | remove unreachable objects, merge duplicates |
| 19 | Create menu background | gradient + drifting symbols + top layer |
| 20 | Loading screen | split `loading.sc` into parts and rebuild with your images |
| 21 | Parts .sc ⇄ .sc | split any `.sc` into PNG parts and rebuild after editing |
| L | Language | switch English / Russian |

## Detailed usage

### 1. PNG → SC
Converts one PNG or every PNG in a folder into an SC2 file.
- **ExportName** – the name the game/mod will use. The hub prints a ready `"FileName": "sc/…", "ExportName": "…"` block.
- **Clip size** – a factor (`0.5` = half, `2` = double) or a final size like `135x100`. The picture is created at its original
  quality and scaled afterwards, so a big pin can be shrunk without losing sharpness.
- **Anchor point** – the point scaling is done from (centre, bottom for objects standing on the ground, corners…).
- **Scale mode** – scale the shape itself (1) or the clip matrix (2).
- **Advanced** – premultiplied alpha, a custom shape size before scaling.

### 2. GIF → SC
Same questions as PNG → SC, plus: ping-pong playback, frame width in the file (smaller = lighter file), first-frame preview.
Any GIF size works; transparency is kept.

### 3. Inspect .sc
Prints a summary (format, chunks, exports, counts) and writes a detailed dump `.txt` to `output/`. Works for SC1 and SC2.

### 4. Rename exports
Rename one ExportName, replace a part of many names, or apply a list from a text file. The file is rewritten in place of
a copy; asks the output name.

### 5. Size and position
Scale (coefficient) and shift (px on X/Y) MovieClips or Shapes. Select by ExportName, a pattern with `*`, a clip id, or a list
file; for shapes by shape ids (`5, 7-9`) or the shapes of a clip. The source fps and coordinates are shown.

### 6. MovieClip FPS
Set the frame rate (1–255) for one clip, all exported clips, or a list.

### 7. JSON/msgpack + PNG ⇄ .sc
Mode 1 decodes any `.sc` (SC1 or SC2) to `data.json` (or `data.msgpack` if JSON would exceed 20 MB) plus the atlas PNGs.
Mode 2 builds an `.sc` back from such a folder after you edit it. The result is verified by decoding it again.

### 8. Edit TextFields
Shows how many texts are non-empty, searches by text (first matches with ids) or takes an id directly; edit the text,
font size and frame. Texts that live in clip names / localization keys are handled too.

### 9. Rebuild atlases
Repacks every shape fragment from scratch into atlases up to 4096 px on a side: maximum atlas size, padding, 90° rotation
for density, dropping pixels outside the shape outlines, dropping objects not reachable from any ExportName.

### 10. Delete exports
Deletes ExportNames together with the clips and shapes used only by them, optionally rebuilding atlases so their pixels vanish too.

### 12–13. Info by ID, SC package
Look up an object by id (parents, exports, text). Find companion files of split packages (`_tex.sc`, `.sctx`/`.zktx`).

### 14. Merge
Merges a folder of `.sc` files into one SC2 (id collisions are renumbered, duplicate export names renamed), or turns one
split package into a single file. Optional ASTC compression.

### 15. Text table (CSV)
Export all export names / texts to CSV, edit them in a spreadsheet, apply back.

### 16. Transfer exports
Move chosen exports (with everything they need) into a new `.sc`, or add them to another file.

### 17. File diagnostics
A metadata-only report you can attach to a bug report (no pixels, no texts).

### 18. Clean up / optimize
Removes unreachable objects and merges identical ones; verifies that every export still has the same appearance fingerprint.

### 19. Create menu background
Builds an animated menu background export (default name `theme_bg`).

Put these files into a folder (see `examples/background/`):

| File | Meaning |
|---|---|
| `gradient.png` | bottom layer (2048×1152 recommended; other sizes e.g. 1848×1112 are fitted, empty edges filled with blur) |
| `symbol.png` | white symbol with alpha, many copies drift across the field |
| `top.png` **or** `top.gif` | overlay on top (2048×1152 recommended, partly transparent; GIF for animation) |
| `flash.png` | optional glow pulsing behind the symbols |

Questions: stage (1 static, 2 gradient + symbols, 3 + top layer), game units per pixel (default fits 1383.12×778 units),
layout (staggered / regular / scattered), density, manual symbol tuning (tilt, spacing, size, speed, opacity, rotation),
flash, top-layer shift, export name, optional ASTC.

### 20. Loading screen
**Mode 1 – split.** Pick your `loading.sc`. The hub cuts it into `output/loading_parts/`:
`background.png`, `logo_EN/KR/JP/CNT/CNS.png`, `supercell_logo.png` (the SUP/ERC/ELL logo), `supercell_shine.png`, icons and
buttons, plus `template.sc`, `parts.json`, `README.txt` and `preview.png`. Parts are cut exactly along the shape polygon, so
neighbouring objects of the atlas never leak in.

**Mode 2 – build.** Edit the PNGs (any size) and/or answer:
- own background (fit: cover / contain / stretch, extra scale),
- one logo for all languages (+ scale),
- SUPERCELL intro logo: keep, replace, or **SUPERCELL → your logo** with a smooth diagonal wipe from the bottom-left to the
  top-right corner (duration in seconds; the intro becomes longer by that time),
- optional ASTC, output name.

Only parts whose PNG changed are replaced. The result is decoded again and a `*_preview.png` is saved.

### 21. Parts .sc ⇄ .sc
The same idea for **any** `.sc`: mode 1 splits all single-rectangle shapes (minimum size is asked, default 12 units) into
PNGs in `output/sc_parts/` with `template.sc` and `parts.json`; mode 2 rebuilds after you edit PNGs (ask how to fit images
whose size changed). Multi-piece shapes and animated-by-frame parts stay as in the template. The output is always SC2.

## Tips and limitations
- Keep originals: edit copies.
- SC1 files are read and converted; deep edits (items 20/21 etc.) write SC2.
- Files with several parts (`_tex.sc`, `.sctx/.zktx`) – see item 13; ASTC is optional because it is lossy and slow.
- Reports/dumps written to files (`.txt`, `.csv`) are always in Russian; the console follows the chosen language.
- English UI strings are machine-assisted translations; corrections are welcome.

## Contributing
Issues and pull requests are welcome. The whole tool is `sc_hub.py`; UI strings are written in Russian in the code and the
English table `I18N_EN` maps each Russian fragment to English (add a pair there when you add a message).

## License
MIT — see [LICENSE](LICENSE).
