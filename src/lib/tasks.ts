import { ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { onSidecarMessage, pingSidecar, taskCommand, type QualityPolicy } from "./sidecar";

export interface TaskConfig {
  url: string;
  title: string;
  policy: QualityPolicy;
  formatLabel: string;
  output_dir: string;
  cookies_from_browser?: string;
  auth_session_id?: string;
  account_ref?: string;
  batchId?: string;
  retryOf?: number;
  conflict_policy?:'keep_both'|'reuse_verified'|'replace';
}
export interface DownloadTask {
  jobId: number;
  sequence: number;
  status: "queued" | "starting" | "downloading" | "processing" | "pausing" | "paused" | "cancelling" | "completed" | "cancelled" | "failed" | "interrupted";
  config: TaskConfig;
  progress: number | null;
  downloadedBytes: number;
  totalBytes: number | null;
  speed: number | null;
  eta: number | null;
  filename: string;
  filepath?: string;
  errorMessage?: string;
  actualQuality?: string;
  storageError?: string;
  errorCode?: string;
  attempt?:number;
  attemptHistory?:Array<{attempt:number;status:string;errorCode?:string}>;
}

export const tasks = ref<DownloadTask[]>([]);
export const engineReady = ref(false);
export const engineError = ref("");
export const historyWarning = ref('');
export const isActive = (task: DownloadTask) => ["queued", "starting", "downloading", "processing", "pausing", "cancelling"].includes(task.status);
export const isTerminal = (task: DownloadTask) => ['completed','cancelled','failed','interrupted'].includes(task.status);
export async function resumeTask(task: DownloadTask) {
  if (task.sequence === 0) return submitTask({...task.config, retryOf:task.jobId});
  merge(await taskCommand<DownloadTask>('resume', {job_id:task.jobId}));
}
const hidden = new Set<number>();
let initialization: Promise<void> | undefined;

function merge(task: DownloadTask) {
  if (hidden.has(task.jobId)) return;
  const index = tasks.value.findIndex((old) => old.jobId === task.jobId);
  if (index < 0) tasks.value.push(task);
  else if (tasks.value[index].sequence < task.sequence) tasks.value[index] = task;
}

export async function refreshTasks() {
  (await taskCommand<DownloadTask[]>("tasks")).forEach(merge);
  const status=await taskCommand<{unreadable_records:number}|undefined>('storage_status');
  historyWarning.value=status?.unreadable_records?`${status.unreadable_records} 条历史记录无法读取，已隔离且保留原数据；可导出脱敏诊断。`:'';
}

export function initializeTasks() {
  if (!initialization) initialization = (async () => {
    await onSidecarMessage((msg) => {
      if (msg.type === "task") merge(msg.data as DownloadTask);
      if (msg.type === "ready") { engineReady.value = true; engineError.value = ""; }
      if (msg.type === 'engine_failure') { engineReady.value=false;engineError.value=String(msg.error||'引擎无法启动'); }
      if (msg.type === "engine_exit") {
        engineReady.value = false;
        engineError.value ||= "下载引擎已退出，可重启后重试任务";
        tasks.value.filter(isActive).forEach((task) => {
          task.status = "interrupted";
          task.errorMessage = engineError.value;
        });
      }
    });
    await checkEngine();
  })();
  return initialization;
}

async function checkEngine() {
  try {
    await pingSidecar();
    engineReady.value = true;
    engineError.value = "";
    await refreshTasks();
  } catch (error) {
    engineReady.value = false;
    engineError.value ||= String(error);
  }
}

export async function restartEngine() {
  await invoke("restart_sidecar");
  await checkEngine();
}

export async function submitTask(config: TaskConfig) {
  await initializeTasks();
  if (!engineReady.value) throw new Error("下载引擎未就绪");
  const jobId = await invoke<number>("alloc_job_id");
  const task: DownloadTask = { jobId, sequence: 0, status: "starting", config: { ...config, policy: { ...config.policy } },
    progress: null, downloadedBytes: 0, totalBytes: null, speed: null, eta: null, filename: "" };
  tasks.value.push(task);
  try {
    merge(await taskCommand<DownloadTask>("download", { ...task.config, job_id: jobId }));
  } catch (error) {
    // A timed-out acknowledgement is not proof that the job was rejected.
    await refreshTasks().catch(() => {});
    const current = tasks.value.find((item) => item.jobId === jobId);
    if (current && current.sequence === 0) {
      current.status = String(error).includes("超时") ? "interrupted" : "failed";
      current.errorMessage = String(error);
    }
    throw error;
  }
}

export async function clearFinished() {
  const localRejected = tasks.value.filter((task) => isTerminal(task) && task.sequence === 0).map((task) => task.jobId);
  const { removed } = await taskCommand<{ removed: number[] }>("clear_finished");
  [...removed, ...localRejected].forEach((id) => hidden.add(id));
  tasks.value = tasks.value.filter((task) => !hidden.has(task.jobId));
}

export async function removeFinished(task: DownloadTask) {
  if (!isTerminal(task)) throw new Error("请先取消任务，再移除记录");
  const { removed } = await taskCommand<{ removed: number[] }>("remove_finished", { job_id: task.jobId });
  if (task.sequence === 0) removed.push(task.jobId);
  removed.forEach((id) => hidden.add(id));
  tasks.value = tasks.value.filter((item) => !hidden.has(item.jobId));
}
