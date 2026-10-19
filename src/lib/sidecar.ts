import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// ── Sidecar message types ──────────────────────────────────────────────

/** Mirror of the JSONL message shape from sidecar/bilio_sidecar/io.py. */
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

/** Per-entry parse progress event. Mirrors the `parse_progress` payload
 *  emitted by the sidecar while `_enrich_entries` resolves each episode. */
export interface ParseProgressEvent {
  index: number;
  total: number;
  entry: ParsedEntry;
}

/** Result of `check_cookies` — what the Bilibili nav API says about the
 *  user's session, packaged with a probe-level `ok`/`error`. */
export interface CookieStatus {
  state?: string;
  ok: boolean;
  logged_in: boolean;
  username: string | null;
  is_vip: boolean;
  vip_label: string | null;
  error: string | null;
}

// ── Parse types (mirror _shape() in ytdlp.py) ──────────────────────────

export interface ParsedFormat {
  format_id: string | null;
  ext: string | null;
  height: number | null;
  /** Width in pixels — paired with `height` for the "尺寸" column. */
  width: number | null;
  vbr: number | null;
  acodec: string | null;
  vcodec: string | null;
  /** Human-readable quality label from yt-dlp's bilibili extractor —
   *  e.g. "1080P 高清", "1080P 高码率", "4K 超高清". The sidecar prefers
   *  the `format` field (which carries this) over `format_note` (None on
   *  Bilibili). Falls back to `format_note` for non-Bilibili sources. */
  format_note: string | null;
  /** B站 qn quality number (80 = 1080P, 112 = 1080P高码, 120 = 4K). */
  quality: number | null;
  filesize: number | null;
}

export interface ParsedEntry {
  id: string | null;
  title: string | null;
  duration: number | null;
  url: string | null;
  detail_status: "pending" | "ok" | "missing_url" | "error" | string | null;
  detail_error: string | null;
  best_format_id: string | null;
  best_format_note: string | null;
  best_width: number | null;
  best_height: number | null;
  best_quality: number | null;
}

export interface ParsedVideo {
  title: string | null;
  /** Human-friendly title used for display; falls back to `title`.
   *  For 番剧 (bangumi), yt-dlp's raw `title` is a bare episode number,
   *  so the sidecar synthesizes "番剧 · 第 N 集" here instead. */
  display_title: string | null;
  /** Content classification from the sidecar:
   *  - `"single"`: standalone video (UP 主投稿)
   *  - `"playlist"`: anthology / collection / multi-part
   *  - `"bangumi"`: a single episode of an anime / drama (no UP 主) */
  kind: "single" | "playlist" | "bangumi" | string;
  uploader: string | null;
  duration: number | null;
  thumbnail: string | null;
  webpage_url: string | null;
  is_playlist: boolean;
  /** Bangumi-only — null for other kinds. */
  episode: string | null;
  episode_number: number | null;
  episode_id: string | null;
  season_id: string | null;
  extractor: string | null;
  entries: ParsedEntry[];
  formats: ParsedFormat[];
}

// ── Invoke wrappers ────────────────────────────────────────────────────

/**
 * Parse a Bilibili URL.
 *
 * Streaming flow:
 *  1. We allocate a job id from Rust.
 *  2. We subscribe to `sidecar://message` before any sidecar work starts.
 *  3. The sidecar emits `parse_progress` × N → terminal
 *     `result`/`error`. We dispatch matching events to `onProgress` and
 *     resolve the promise on the terminal message.
 *
 * `cookiesFromBrowser` makes yt-dlp read the user's logged-in cookies so
 * private / premium-quality content resolves. Anonymous parse omits it.
 */
export async function parseUrl(
  url: string,
  cookiesFromBrowser?: string,
  onProgress?: (e: ParseProgressEvent) => void,
  signal?: AbortSignal,
): Promise<ParsedVideo> {
  const jobId = await invoke<number>("alloc_job_id");
  if (signal?.aborted) throw new Error("已取消解析");
  let resolveResult: ((v: ParsedVideo) => void) | null = null;
  let rejectResult: ((e: Error) => void) | null = null;
  const resultPromise = new Promise<ParsedVideo>((res, rej) => {
    resolveResult = res;
    rejectResult = rej;
  });
  // Attach before invoking so an immediate terminal event is never unhandled.
  void resultPromise.catch(() => {});
  let timer: ReturnType<typeof setTimeout> | undefined;
  const resetTimeout = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      void cancelDownload(jobId).catch(() => {});
      rejectResult?.(new Error("解析长时间无响应，已请求取消，请重试"));
    }, 90000);
  };

  const unlisten = await onSidecarMessage((msg) => {
    if (msg.type === "engine_exit") {
      rejectResult?.(new Error("下载引擎已退出，请重启引擎后重试"));
      return;
    }
    if (msg.id !== jobId) return;
    if (msg.type === "parse_progress") {
      resetTimeout();
      const data = msg.data as ParseProgressEvent | undefined;
      if (data && onProgress) onProgress(data);
    } else if (msg.type === "result") {
      resolveResult?.((msg.data as ParsedVideo) ?? (msg as unknown as ParsedVideo));
    } else if (msg.type === "error") {
      rejectResult?.(new Error(String(msg.error ?? "解析失败")));
    }
  });

  const abort = () => {
    void cancelDownload(jobId).catch(() => {});
    rejectResult?.(new Error("已取消解析"));
  };
  signal?.addEventListener("abort", abort, { once: true });

  try {
    if (signal?.aborted) throw new Error("已取消解析");
    resetTimeout();
    await invoke("start_parse", {
      jobId,
      url,
      cookiesFromBrowser: cookiesFromBrowser ?? null,
    });
    if (signal?.aborted) abort();
    return await resultPromise;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    unlisten();
  }
}

export interface PongResponse {
  id: number;
  type: "pong";
  ts: number;
}

export function pingSidecar(): Promise<PongResponse> {
  return invoke<PongResponse>("ping_sidecar");
}

/** Check whether the chosen browser's Bilibili cookies represent a
 *  logged-in session. Returns the full status dict — non-VIP, logged-out,
 *  and brand-new-or-no-browser sessions all return a valid shape rather
 *  than throwing, so callers can render the badge unconditionally. */
export function checkCookies(cookiesFromBrowser?: string): Promise<CookieStatus> {
  return invoke<CookieStatus>("check_cookies", {
    cookiesFromBrowser: cookiesFromBrowser ?? null,
  });
}

/** Compiled and validated by the sidecar, never an arbitrary selector string. */
export type QualityPolicy = { mode: "best" } | { mode: "max_height"; height: number } | { mode: "max_quality"; quality:number } | { mode: "exact"; format_id: string };

export function taskCommand<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  return invoke<T>("task_command", { command, args });
}

/** Cancel a running download identified by its job_id. */
export function cancelDownload(jobId: number): Promise<unknown> {
  return taskCommand("cancel", { job_id: jobId });
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
    let message: SidecarMessage;
    try {
      message = JSON.parse(event.payload) as SidecarMessage;
    } catch {
      return;
    }
    cb(message);
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
