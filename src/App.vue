<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  NButton,
  NConfigProvider,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NInput,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NMessageProvider,
  NTag,
  dateZhCN,
  zhCN,
} from "naive-ui";
import MainArea from "./components/MainArea.vue";
import { onSidecarMessage, type SidecarMessage } from "./lib/sidecar";

const sidecarReady = ref(false);
const settingsOpen = ref(false);
const downloadDir = ref(localStorage.getItem("yabi-download-dir") ?? "~/Downloads/Yabi");

onMounted(async () => {
  await onSidecarMessage((msg: SidecarMessage) => {
    if (msg.type === "ready") sidecarReady.value = true;
  });
});

function saveDownloadDir(val: string) {
  downloadDir.value = val;
  localStorage.setItem("yabi-download-dir", val);
}
</script>

<template>
  <n-config-provider :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-layout style="min-height: 100vh">
        <n-layout-header bordered class="header">
          <div class="brand">
            <h1>Yabi</h1>
            <span class="tagline">基于 yt-dlp 的 B站视频下载 GUI</span>
          </div>
          <div class="header-right">
            <span class="status" :class="sidecarReady ? 'ok' : 'pending'">
              {{ sidecarReady ? "● sidecar ready" : "○ starting…" }}
            </span>
            <n-button quaternary size="tiny" @click="settingsOpen = true">
              ⚙️
            </n-button>
          </div>
        </n-layout-header>
        <n-layout-content class="content">
          <MainArea :download-dir="downloadDir" />
        </n-layout-content>
      </n-layout>

      <n-drawer v-model:show="settingsOpen" :width="360" placement="right">
        <n-drawer-content title="设置" closable>
          <n-descriptions :columns="1" label-placement="top" bordered size="small">
            <n-descriptions-item label="下载目录">
              <n-input
                :value="downloadDir"
                @update:value="saveDownloadDir"
                placeholder="~/Downloads/Yabi"
                size="small"
              />
            </n-descriptions-item>
            <n-descriptions-item label="ffmpeg">
              <n-tag size="small" type="info">imageio-ffmpeg 自动定位</n-tag>
            </n-descriptions-item>
          </n-descriptions>
          <p class="settings-note">
            下载目录通过 localStorage 持久化。ffmpeg 由 imageio-ffmpeg 自动定位。
          </p>
        </n-drawer-content>
      </n-drawer>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.header {
  padding: 0.5rem 1.25rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}
.brand h1 {
  margin: 0;
  font-size: 1.3rem;
  letter-spacing: 0.5px;
}
.tagline {
  font-size: 0.85rem;
  color: var(--n-text-color-3, #888);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.status {
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}
.status.ok {
  color: #22a06b;
}
.status.pending {
  color: #b07d00;
}
.content {
  padding: 1rem 1.5rem;
  max-width: 960px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}
.settings-note {
  font-size: 0.8rem;
  color: #888;
  margin-top: 1rem;
}
</style>
<style>
:root {
  font-family:
    -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", Roboto, sans-serif;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
html,
body,
#app {
  height: 100%;
  margin: 0;
}
</style>