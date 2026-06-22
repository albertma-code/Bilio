<script setup lang="ts">
import { computed, h, ref } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NImage,
  NInput,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import {
  formatDuration,
  parseUrl,
  type ParsedEntry,
  type ParsedFormat,
  type ParsedVideo,
} from "../lib/sidecar";

const props = defineProps<{ cookiesBrowser?: string }>();

const url = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const info = ref<ParsedVideo | null>(null);
const message = useMessage();

/** Selected entry ids when info is a playlist. Used by batch download. */
const selectedEntryIds = ref<Array<string | null>>([]);

/**
 * Batch quality selection. Maps to yt-dlp's format spec — for collections
 * the per-entry format ids are unknown until each one is resolved, so we
 * use a generic "best video ≤ N height + best audio" expression.
 */
const batchQuality = ref<string>("bv*[height<=1080]+ba/best");
const batchQualityOptions = [
  { label: "最高清晰度", value: "bv*+ba/best" },
  { label: "≤ 1080p", value: "bv*[height<=1080]+ba/best" },
  { label: "≤ 720p", value: "bv*[height<=720]+ba/best" },
  { label: "≤ 480p", value: "bv*[height<=480]+ba/best" },
  { label: "≤ 360p（最小）", value: "bv*[height<=360]+ba/best" },
];

const canSubmit = computed(() => url.value.trim().length > 0 && !loading.value);

const emit = defineEmits<{
  (e: "download", url: string, formatId: string, formatLabel: string): void;
  (
    e: "batch-download",
    entries: Array<{ url: string; title: string }>,
    formatId: string,
    formatLabel: string,
  ): void;
}>();

async function onParse() {
  if (!canSubmit.value) return;
  loading.value = true;
  error.value = null;
  info.value = null;
  selectedEntryIds.value = [];
  try {
    info.value = await parseUrl(url.value.trim(), props.cookiesBrowser);
    if (info.value?.is_playlist) {
      // Default-select all entries — typical use case is "download whole collection".
      selectedEntryIds.value = info.value.entries
        .map((e) => e.id)
        .filter((id): id is string => id != null);
    }
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

function toggleEntry(id: string | null) {
  if (id == null) return;
  const i = selectedEntryIds.value.indexOf(id);
  if (i >= 0) selectedEntryIds.value.splice(i, 1);
  else selectedEntryIds.value.push(id);
}

function toggleAll() {
  if (!info.value) return;
  if (selectedEntryIds.value.length === info.value.entries.length) {
    selectedEntryIds.value = [];
  } else {
    selectedEntryIds.value = info.value.entries.map((e) => e.id).filter((x): x is string => x != null);
  }
}

const allSelected = computed(
  () => !!info.value && selectedEntryIds.value.length === info.value.entries.length && info.value.entries.length > 0,
);

function doBatchDownload() {
  if (!info.value) return;
  const selected = info.value.entries.filter(
    (e) => e.id != null && selectedEntryIds.value.includes(e.id),
  );
  if (!selected.length) {
    message.warning("请先选择要下载的分集");
    return;
  }
  // Each entry has its own URL (set by yt-dlp on flat extract). Fall back to
  // the source URL — yt-dlp will then download the whole playlist via index.
  const items = selected
    .map((e) => ({ url: e.url ?? url.value.trim(), title: e.title ?? "(无标题)" }))
    .filter((x) => x.url);
  const qualityLabel =
    batchQualityOptions.find((o) => o.value === batchQuality.value)?.label ?? batchQuality.value;
  emit("batch-download", items, batchQuality.value, qualityLabel);
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

const entryColumns = computed<DataTableColumns<ParsedEntry>>(() => [
  {
    title: () =>
      h(NCheckbox, {
        checked: allSelected.value,
        onUpdateChecked: toggleAll,
      }),
    key: "_select",
    width: 50,
    render: (row) =>
      h(NCheckbox, {
        checked: row.id != null && selectedEntryIds.value.includes(row.id),
        onUpdateChecked: () => toggleEntry(row.id),
      }),
  },
  { title: "#", key: "_idx", width: 50, render: (_row, idx) => idx + 1 },
  { title: "标题", key: "title" },
  {
    title: "时长",
    key: "duration",
    width: 100,
    render: (row) => formatDuration(row.duration),
  },
]);
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

        <!-- Playlist / collection: per-entry selection + batch download -->
        <n-descriptions
          v-if="info.is_playlist && info.entries.length"
          :columns="1"
          label-placement="top"
          bordered
          style="margin-top: 1rem"
        >
          <n-descriptions-item :label="`分集列表（已选 ${selectedEntryIds.length} / ${info.entries.length}）`">
            <n-data-table
              :columns="entryColumns"
              :data="info.entries"
              :row-key="(r: ParsedEntry) => r.id ?? ''"
              :max-height="300"
              size="small"
            />
            <n-space style="margin-top: 0.75rem" align="center">
              <span class="batch-label">清晰度</span>
              <n-select
                v-model:value="batchQuality"
                :options="batchQualityOptions"
                size="small"
                style="width: 200px"
              />
              <n-button
                type="primary"
                size="small"
                :disabled="!selectedEntryIds.length"
                @click="doBatchDownload"
              >
                批量下载 ({{ selectedEntryIds.length }})
              </n-button>
            </n-space>
          </n-descriptions-item>
        </n-descriptions>

        <!-- Single video or per-format manual select -->
        <n-descriptions
          v-if="info.formats.length"
          :columns="1"
          label-placement="top"
          bordered
          style="margin-top: 1rem"
        >
          <n-descriptions-item :label="info.is_playlist ? '本视频可选格式（仅当前展开的视频）' : '可选格式'">
            <n-data-table
              :columns="formatColumns"
              :data="info.formats"
              :row-key="(r: ParsedFormat) => r.format_id ?? ''"
              :max-height="300"
              size="small"
            />
          </n-descriptions-item>
        </n-descriptions>

        <n-empty
          v-if="!info.formats.length && !info.entries.length"
          description="没有解析到可用格式或分集"
        />
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
.batch-label {
  font-size: 0.85rem;
  color: var(--n-text-color-2, #666);
}
</style>