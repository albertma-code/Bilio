# Third-Party Components

This is a local, ad-hoc-signed candidate, not an approved public distribution.
The Bilio source license does not replace the licenses of bundled components.

| Component | Candidate Source | Notice |
| --- | --- | --- |
| yt-dlp | Pinned Python package 2025.10.14 | Refer to upstream LICENSE and bundled dependency notices: https://github.com/yt-dlp/yt-dlp |
| imageio-ffmpeg | Pinned Python package 0.6.0 | Package code and the ffmpeg executable have different licenses: https://github.com/imageio/imageio-ffmpeg |
| ffmpeg | Bundled macOS arm64 executable, version 7.1 | The actual `ffmpeg -L` output identifies GNU GPL version 2 or later. Do not describe this binary as MIT or assume an LGPL-only build. |
| PyInstaller | 6.21.0 | Upstream license and bootloader exception apply: https://github.com/pyinstaller/pyinstaller |
| Tauri / Vue / Naive UI / Lucide | Versions locked in Cargo.lock and package-lock.json | Their respective upstream licenses and notices apply. |

Before public redistribution, verify and include the exact dependency license
texts, ffmpeg corresponding-source/build obligations, and notices required by
the actual binaries. These links and this inventory alone are not a completed
redistribution-compliance audit. No public release is authorized in this round.
