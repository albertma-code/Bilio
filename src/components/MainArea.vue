<script setup lang="ts">
import { ref } from "vue";
import { useMessage } from "naive-ui";
import DownloadManager from "./DownloadManager.vue";
import UrlParser from "./UrlParser.vue";
import { downloadVideo } from "../lib/sidecar";

/**
 * The "main area" of the app, separated from App.vue so that `useMessage()`
 * can run inside the `<n-message-provider>` ancestor that App.vue installs.
 * (useMessage / useNotification / useDialog all require a provider above
 * them in the component tree.)
 */

const props = defineProps<{
  downloadDir: string;
  cookiesBrowser?: string;
}>();

const message = useMessage();
const managerRef = ref<InstanceType<typeof DownloadManager> | null>(null);

async function startOne(url: string, formatId: string, title: string, formatLabel: string) {
  const jobId = await downloadVideo(url, formatId, props.downloadDir, props.cookiesBrowser);
  managerRef.value?.registerDownload(jobId, url, title, `${formatId} (${formatLabel})`);
  return jobId;
}

/** Single-video download (the "下载" button in the formats table). */
async function handleDownload(url: string, formatId: string, formatLabel: string) {
  try {
    const display = url.length > 60 ? url.slice(0, 60) + "…" : url;
    await startOne(url, formatId, display, formatLabel);
    message.success("下载任务已提交");
  } catch (e: any) {
    message.error(`下载启动失败: ${typeof e === "string" ? e : e?.message ?? "未知错误"}`);
  }
}

/**
 * Batch download for collection/playlist entries.
 * yt-dlp resolves each entry URL to its own format list at download time;
 * passing `format_id` as `bv*[height<=$max]+ba/best` lets yt-dlp pick the
 * best stream up to the requested quality, which is the right behavior for
 * mixed-quality collections.
 */
async function handleBatchDownload(
  entries: Array<{ url: string; title: string }>,
  formatId: string,
  formatLabel: string,
) {
  if (!entries.length) return;
  message.info(`已提交 ${entries.length} 个分集到下载队列`);
  let ok = 0;
  let fail = 0;
  for (const entry of entries) {
    try {
      await startOne(entry.url, formatId, entry.title, formatLabel);
      ok++;
    } catch (e: any) {
      fail++;
      // Continue with the rest of the batch
    }
  }
  if (fail > 0) {
    message.warning(`${ok} 个已提交，${fail} 个失败`);
  } else {
    message.success(`全部 ${ok} 个分集已提交`);
  }
}
</script>

<template>
  <UrlParser
    :cookies-browser="cookiesBrowser"
    @download="handleDownload"
    @batch-download="handleBatchDownload"
  />
  <DownloadManager ref="managerRef" />
</template>