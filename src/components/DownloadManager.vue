<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { type UnlistenFn } from "@tauri-apps/api/event";
import { NCard, NButton, NProgress, NTag } from "naive-ui";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import {
  cancelDownload,
  formatBytes,
  formatEta,
  formatSpeed,
  onSidecarMessage,
  type DownloadProgress,
  type SidecarMessage,
} from "../lib/sidecar";

export interface DownloadTask {
  jobId: number;
  url: string;
  title: string;
  formatLabel: string;
  status: "starting" | "downloading" | "finished" | "cancelled" | "error";
  progress: number; // 0–100
  downloadedBytes: number;
  totalBytes: number | null;
  speed: number | null;
  eta: number | null;
  filename: string;
  filepath?: string;
  errorMessage?: string;
}

const tasks = ref<DownloadTask[]>([]);
let unlisten: UnlistenFn | null = null;

function addTask(jobId: number, url: string, title: string, formatLabel: string) {
  tasks.value.push({
    jobId,
    url,
    title,
    formatLabel,
    status: "starting",
    progress: 0,
    downloadedBytes: 0,
    totalBytes: null,
    speed: null,
    eta: null,
    filename: "",
  });
}

function getTask(jobId: number): DownloadTask | undefined {
  return tasks.value.find((t) => t.jobId === jobId);
}

interface DownloadResultData {
  status?: string;
  filepath?: string;
}

function handleSidecar(msg: SidecarMessage) {
  const jobId = msg.id;
  if (typeof jobId !== "number") return;
  const task = getTask(jobId);
  if (!task) return;

  if (msg.type === "progress") {
    const p = msg.data as DownloadProgress;
    task.status = "downloading";
    task.progress = p.total_bytes ? Math.round((p.downloaded_bytes / p.total_bytes) * 100) : 0;
    task.downloadedBytes = p.downloaded_bytes;
    task.totalBytes = p.total_bytes;
    task.speed = p.speed;
    task.eta = p.eta;
    task.filename = p.filename;
  } else if (msg.type === "result") {
    const d = msg.data as DownloadResultData;
    if (d.status === "completed") {
      task.status = "finished";
      task.progress = 100;
      if (d.filepath) task.filepath = d.filepath;
    } else if (d.status === "cancelled") {
      task.status = "cancelled";
    }
  } else if (msg.type === "error") {
    task.status = "error";
    task.errorMessage = (msg.error as string) || "未知错误";
  }
}

onMounted(async () => {
  unlisten = await onSidecarMessage(handleSidecar);
});

onUnmounted(() => {
  unlisten?.();
});

async function doCancel(task: DownloadTask) {
  try {
    await cancelDownload(task.jobId);
  } catch {
    // If the sidecar already finished the cancel will be a no-op
  }
}

/** Reveal the downloaded file in Finder, falling back to its containing dir. */
async function openFolder(task: DownloadTask) {
  if (task.filepath) {
    try {
      await revealItemInDir(task.filepath);
    } catch (e) {
      // ignore — best-effort
    }
  }
}

/** Expose a register function so UrlParser can add tasks. */
function registerDownload(jobId: number, url: string, title: string, formatLabel: string) {
  addTask(jobId, url, title, formatLabel);
}

defineExpose({ registerDownload });
</script>

<template>
  <n-card v-if="tasks.length" title="下载任务" :bordered="false" class="manager-card">
    <div v-for="task in tasks" :key="task.jobId" class="task-card" :class="task.status">
      <div class="task-header">
        <span class="task-title">{{ task.title }}</span>
        <n-tag
          :type="task.status === 'finished' ? 'success' : task.status === 'error' ? 'error' : task.status === 'cancelled' ? 'warning' : 'info'"
          size="small"
        >
          {{
            task.status === "starting" ? "启动中…" :
            task.status === "downloading" ? "下载中" :
            task.status === "finished" ? "已完成" :
            task.status === "cancelled" ? "已取消" : "失败"
          }}
        </n-tag>
      </div>

      <n-progress
        v-if="task.status === 'downloading' || task.status === 'starting'"
        type="line"
        :percentage="task.progress"
        :show-indicator="true"
        :height="10"
        :border-radius="5"
        processing
      />

      <div v-if="task.status === 'downloading'" class="task-stats">
        <span>{{ formatBytes(task.downloadedBytes) }} / {{ formatBytes(task.totalBytes) }}</span>
        <span>{{ formatSpeed(task.speed) }}</span>
        <span>剩余 {{ formatEta(task.eta) }}</span>
      </div>

      <div class="task-footer">
        <n-button
          v-if="task.status === 'downloading' || task.status === 'starting'"
          size="tiny"
          quaternary
          type="error"
          @click="doCancel(task)"
        >
          取消
        </n-button>
        <n-button
          v-if="task.status === 'finished'"
          size="tiny"
          quaternary
          type="primary"
          @click="openFolder(task)"
        >
          打开文件夹
        </n-button>
      </div>

      <p v-if="task.status === 'error'" class="task-error">{{ task.errorMessage }}</p>
    </div>
  </n-card>
</template>

<style scoped>
.manager-card {
  margin-top: 1rem;
}
.task-card {
  border: 1px solid #eee;
  border-radius: 10px;
  padding: 0.75rem;
  margin-bottom: 0.5rem;
  background: var(--n-color, #fafafa);
}
.task-card:last-child {
  margin-bottom: 0;
}
.task-card.error {
  border-left: 3px solid #d03050;
}
.task-card.finished {
  border-left: 3px solid #18a058;
}
.task-card.cancelled {
  border-left: 3px solid #f0a020;
}
.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.3rem;
}
.task-title {
  font-weight: 500;
  font-size: 0.9rem;
  word-break: break-word;
  flex: 1;
  margin-right: 0.5rem;
}
.task-stats {
  display: flex;
  gap: 1rem;
  font-size: 0.8rem;
  color: #888;
  margin-top: 0.25rem;
}
.task-footer {
  margin-top: 0.3rem;
}
.task-error {
  font-size: 0.8rem;
  color: #d03050;
  margin: 0.25rem 0 0;
}
@media (prefers-color-scheme: dark) {
  .task-card {
    border-color: #333;
    background: #1f1f1f;
  }
}
</style>