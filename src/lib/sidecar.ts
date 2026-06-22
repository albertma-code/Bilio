import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// ── Sidecar message types ──────────────────────────────────────────────

/** Mirror of the JSONL message shape from sidecar/yabi_sidecar/io.py. */
export interface SidecarMessage {
  id: number | null;
  type: string;
  [key: string]: unknown;
}

/** Progress fields sent by the sidecar's download worker thread. */
export interface DownloadProgress {
  status: "downloading" | "finished";
  downloaded_bytes: number;
  total_bytes: number | null;
  speed: number | null;
  eta: number | null;
  filename: string;
  percent: string;
}

// ── Parse types (mirror _shape() in ytdlp.py) ──────────────────────────

export interface ParsedFormat {
  format_id: string | null;
  ext: string | null;
  height: number | null;
  vbr: number | null;
  acodec: string | null;
  vcodec: string | null;
  format_note: string | null;
  filesize: number | null;
}

export interface ParsedEntry {
  id: string | null;
  title: string | null;
  duration: number | null;
  url: string | null;
}

export interface ParsedVideo {
  title: string | null;
  uploader: string | null;
  duration: number | null;
  thumbnail: string | null;
  webpage_url: string | null;
  is_playlist: boolean;
  entries: ParsedEntry[];
  formats: ParsedFormat[];
}

// ── Invoke wrappers ────────────────────────────────────────────────────

/**
 * Parse a Bilibili URL and return structured metadata.
 * Throws on error — callers surface via Naive UI's notification.
 *
 * `cookiesFromBrowser` (chrome|safari|firefox|edge|brave|…) makes yt-dlp
 * read the user's logged-in cookies so private / premium-quality content
 * resolves. Anonymous parse omits it.
 */
export function parseUrl(
  url: string,
  cookiesFromBrowser?: string,
): Promise<ParsedVideo> {
  return invoke<ParsedVideo>("parse_url", { url, cookiesFromBrowser });
}

export interface PongResponse {
  id: number;
  type: "pong";
  ts: number;
}

export function pingSidecar(): Promise<PongResponse> {
  return invoke<PongResponse>("ping_sidecar");
}

/**
 * Start downloading a video. Returns a `job_id` immediately (sent every
 * progress and terminal event as `msg.id`). The download runs on the
 * sidecar's worker thread; progress events arrive on the `sidecar://message`
 * event bus.
 */
export function downloadVideo(
  url: string,
  formatId: string,
  outputDir: string,
  cookiesFromBrowser?: string,
): Promise<number> {
  return invoke<number>("download_video", {
    url,
    formatId,
    outputDir,
    cookiesFromBrowser,
  });
}

/** Cancel a running download identified by its job_id. */
export function cancelDownload(jobId: number): Promise<unknown> {
  return invoke("cancel_download", { jobId });
}

// ── Event subscriptions ────────────────────────────────────────────────

/**
 * Subscribe to all sidecar messages (lifecycle, progress, terminal).
 * Returns an unlisten function — call on component unmount.
 */
export function onSidecarMessage(
  cb: (msg: SidecarMessage) => void,
): Promise<UnlistenFn> {
  return listen<string>("sidecar://message", (event) => {
    try {
      cb(JSON.parse(event.payload) as SidecarMessage);
    } catch {
      // Non-JSON line is a protocol violation — ignore.
    }
  });
}

// ── UI helpers ─────────────────────────────────────────────────────────

/** Format seconds as MM:SS / HH:MM:SS. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return "--:--";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(ss)}` : `${m}:${pad(ss)}`;
}

/** Format bytes into a human-readable string. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

/** Format speed (bytes/sec) into a human-readable string. */
export function formatSpeed(speed: number | null | undefined): string {
  if (speed == null) return "--";
  return `${formatBytes(speed)}/s`;
}

/** Format ETA (seconds) into a human-readable string. */
export function formatEta(eta: number | null | undefined): string {
  if (eta == null) return "--:--";
  const s = Math.floor(eta);
  const m = Math.floor(s / 60);
  const ss = s % 60;
  return `${m}:${ss.toString().padStart(2, "0")}`;
}