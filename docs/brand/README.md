# Bilio Icon

Selected on 2026-10-19: teal background, ivory archival pocket, coral video tile.

Current source: `bilio-icon-v2.png` (1254 x 1254, PNG with alpha).
Generation prompt: `bilio-icon-v2-prompt.txt`.

Desktop application resources in `src-tauri/icons/` were generated with:

```sh
npx tauri icon docs/brand/bilio-icon-v2.png --output /tmp/bilio-icons-v2
find /tmp/bilio-icons-v2 -maxdepth 1 -type f -exec cp {} src-tauri/icons/ \;
```

Only desktop resources are copied. This project does not currently target mobile.
The old `src-tauri/icon-source.svg` is retained for reference and is not the current source.
Rebuild and reinstall the application to update the installed Dock icon.
