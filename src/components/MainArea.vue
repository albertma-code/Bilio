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

const props = defineProps<{ downloadDir: string }>();

const message = useMessage();
const managerRef = ref<InstanceType<typeof DownloadManager> | null>(null);

async function handleDownload(url: string, formatId: string, formatLabel: string) {
  try {
    const jobId = await downloadVideo(url, formatId, props.downloadDir);
    const displayTitle = url.length > 60 ? url.slice(0, 60) + "…" : url;
    managerRef.value?.registerDownload(jobId, url, displayTitle, `${formatId} (${formatLabel})`);
    message.success("下载任务已提交");
  } catch (e: any) {
    message.error(`下载启动失败: ${typeof e === "string" ? e : e?.message ?? "未知错误"}`);
  }
}
</script>

<template>
  <UrlParser @download="handleDownload" />
  <DownloadManager ref="managerRef" />
</template>