import type { DownloadTask } from "./tasks";
import type { ParsedFormat } from "./sidecar";

export function codecName(codec: string | null | undefined) {
  if (codec?.startsWith("avc")) return "H.264";
  if (codec?.startsWith("hev") || codec?.startsWith("hvc")) return "HEVC";
  if (codec?.startsWith("av01")) return "AV1";
  return codec && codec !== "none" ? codec : "默认";
}

export function qualityName(format: ParsedFormat) {
  return format.format_note || (format.height ? `${format.height}p` : "视频");
}

export function taskFilename(task: DownloadTask) {
  if (task.status === "completed" && task.filepath) return task.filepath.split(/[\\/]/).pop() || "";
  return "";
}

export function taskQuality(task: DownloadTask) {
  return task.config.formatLabel.replace(/ · \d+(?:\+\d+)*$/, "");
}

export function actualQuality(task: DownloadTask) {
  if (!task.actualQuality) return "";
  return task.actualQuality.split("+")[0].replace(/\s*-\s*video only$/, "");
}
