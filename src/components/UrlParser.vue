<script setup lang="ts">
import { computed, h, ref } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NImage,
  NInput,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import {
  formatDuration,
  parseUrl,
  type ParsedFormat,
  type ParsedVideo,
} from "../lib/sidecar";

const url = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const info = ref<ParsedVideo | null>(null);
const message = useMessage();

const canSubmit = computed(() => url.value.trim().length > 0 && !loading.value);

const emit = defineEmits<{
  (e: "download", url: string, formatId: string, formatLabel: string): void;
}>();

async function onParse() {
  if (!canSubmit.value) return;
  loading.value = true;
  error.value = null;
  info.value = null;
  try {
    info.value = await parseUrl(url.value.trim());
    message.success("解析成功");
  } catch (e: any) {
    error.value = typeof e === "string" ? e : e?.message ?? "未知错误";
  } finally {
    loading.value = false;
  }
}

function doDownload(row: ParsedFormat) {
  if (!url.value.trim()) return;
  const label = row.height
    ? `${row.height}p · ${row.format_id}`
    : row.format_note
      ? `${row.format_note} · ${row.format_id}`
      : row.format_id ?? "unknown";
  emit("download", url.value.trim(), row.format_id ?? "", label);
}

const formatColumns: DataTableColumns<ParsedFormat> = [
  { title: "ID", key: "format_id", width: 100 },
  {
    title: "说明",
    key: "height",
    width: 100,
    render: (row) =>
      row.height
        ? `${row.height}p`
        : row.format_note ?? "音频",
  },
  { title: "容器", key: "ext", width: 70 },
  {
    title: "大小",
    key: "filesize",
    width: 100,
    render: (row) =>
      row.filesize != null
        ? `${(row.filesize / 1024 / 1024).toFixed(1)} MB`
        : "—",
  },
  { title: "视频编码", key: "vcodec", width: 110 },
  { title: "音频编码", key: "acodec", width: 110 },
  {
    title: "",
    key: "_action",
    width: 90,
    render: (row) =>
      row.vcodec && row.vcodec !== "none"
        ? h(NButton, { size: "tiny", type: "primary", onClick: () => doDownload(row) }, () => "下载")
        : null,
  },
];

</script>

<template>
  <n-card title="解析 B站链接" :bordered="false" class="parser-card">
    <div class="input-row">
      <n-input
        v-model:value="url"
        placeholder="粘贴 B站视频 / 番剧 / 合集链接（如 https://www.bilibili.com/video/BV...）"
        clearable
        :disabled="loading"
        @keydown.enter="onParse"
      />
      <n-button type="primary" :loading="loading" :disabled="!canSubmit" @click="onParse">
        解析
      </n-button>
    </div>

    <n-alert v-if="error" type="error" :show-icon="true" style="margin-top: 1rem">
      {{ error }}
    </n-alert>

    <n-spin :show="loading">
      <div v-if="info" class="result">
        <div class="header">
          <n-image
            v-if="info.thumbnail"
            :src="info.thumbnail"
            width="200"
            object-fit="cover"
            class="thumb"
            preview-disabled
          />
          <div class="meta">
            <h3>{{ info.title || "(无标题)" }}</h3>
            <p class="uploader">
              UP 主：<strong>{{ info.uploader || "—" }}</strong>
            </p>
            <p class="badges">
              <n-tag :type="info.is_playlist ? 'info' : 'default'" size="small">
                {{ info.is_playlist ? "合集 / 列表" : "单个视频" }}
              </n-tag>
              <n-tag size="small">时长 {{ formatDuration(info.duration) }}</n-tag>
              <n-tag size="small">{{ info.formats.length }} 种格式</n-tag>
              <n-tag v-if="info.is_playlist" size="small">
                {{ info.entries.length }} 个分集
              </n-tag>
            </p>
            <a v-if="info.webpage_url" :href="info.webpage_url" target="_blank" class="source-link">
              {{ info.webpage_url }}
            </a>
          </div>
        </div>

        <n-descriptions
          v-if="info.formats.length"
          :columns="1"
          label-placement="top"
          bordered
          style="margin-top: 1rem"
        >
          <n-descriptions-item label="可选格式">
            <n-data-table
              :columns="formatColumns"
              :data="info.formats"
              :row-key="(r: ParsedFormat) => r.format_id ?? ''"
              :max-height="300"
              size="small"
            />
          </n-descriptions-item>
        </n-descriptions>

        <n-empty v-if="!info.formats.length" description="没有解析到可用格式" />
      </div>
    </n-spin>
  </n-card>
</template>

<style scoped>
.parser-card {
  margin-top: 1rem;
}
.input-row {
  display: flex;
  gap: 0.5rem;
}
.input-row :deep(.n-input) {
  flex: 1;
}
.result {
  margin-top: 1rem;
}
.header {
  display: flex;
  gap: 1rem;
}
.thumb :deep(img) {
  border-radius: 8px;
}
.meta {
  flex: 1;
  min-width: 0;
}
.meta h3 {
  margin: 0 0 0.5rem 0;
  font-size: 1.1rem;
  line-height: 1.4;
  word-break: break-word;
}
.uploader {
  margin: 0.25rem 0;
  color: var(--n-text-color-2, #888);
  font-size: 0.9rem;
}
.badges {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin: 0.5rem 0;
}
.source-link {
  font-size: 0.8rem;
  color: #888;
  text-decoration: none;
  word-break: break-all;
}
.source-link:hover {
  text-decoration: underline;
}
</style>