# Changelog

Versions follow the pencil_cleaner package. The comic-cropper browser page has no package version.

## 0.6.1

- `comics-tools --selftest` smoke-tests a packaged build headlessly: import a synthetic
  scan, detect panels, set per-page settings, save and reload, and open the window.
  The release workflow runs it on Linux, macOS, and Windows before packing each binary.
- A plain string passed as tags now splits on commas instead of becoming one tag per
  letter (`tags="cover, hero"` is `["cover", "hero"]`, not `['c','o','v','e','r',...]`).

## 0.6.0

- Multi-page workspace: a Pages strip with drag-to-reorder, Add, Duplicate, Delete,
  Move earlier/later, and per-page settings (name, US Letter / A4 / custom size,
  orientation, margins, gutter, rows and columns). Projects save as version 3;
  older projects open with their layout as the defaults.
- Review pass (`Ctrl+Shift+R`): step every clip with the arrow keys, see the crop
  as it will print beside the page it came from, `E` edit the crop on the scan,
  `I` flip pencil/ink, `D` delete the clip.
- Pure black and white export for laser printers: a checkbox and threshold turn
  finished pages into two-tone output; the alignment ghost stays pale gray.
- Read lettering runs in the background, so the window never freezes on Tesseract.
  Notes and lettering save per pause, so undo steps come per pause, not per letter.
- EXIF orientation is applied on import, PDF pages past nine import in order, and
  catalog and CSV writes are atomic, like project.json already was.
- Re-linking a take clears the page size its old alignment left behind.

## 0.5.0

- The desktop window covers a working day: import, deskew, panel boxes, board,
  catalog ids, tags, lettering, ink takes lined up on their pencils, honest undo.

## Earlier

- See `git log` and the README. The window grew out of the command-line tools,
  which are unchanged.
