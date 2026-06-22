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
  type ParseProgressEvent,
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

/** Per-entry enrichment progress. Set to `{done, total}` while parse is
 *  running so the UI can render "已解析 N / M"; null when idle. */
const parseProgress = ref<{ done: number; total: number } | null>(null);

/** Selected entry ids when info is a playlist. Used by batch download. */
const selectedEntryIds = ref<Array<string | null>>([]);

/**
 * Batch quality selection. Maps to yt-dlp's format spec — for collections
 * the per-entry format ids are unknown until each one is resolved, so we
 * use a generic "best video ≤ N height + best audio" expression.
 */
const batchQuality = ref<string>("bv*+ba/best");
const batchQualityOptions = [
  { label: "最高可用清晰度", value: "bv*+ba/best" },
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
  parseProgress.value = null;

  // Preview the skeleton immediately so the user sees the entry list and
  // format table without waiting for per-entry enrichment. We create a
  // placeholder info, then merge progress events as they arrive.
  const initialInfo: ParsedVideo = {
    title: null,
    display_title: "解析中…",
    kind: "playlist",
    uploader: null,
    duration: null,
    thumbnail: null,
    webpage_url: url.value.trim(),
    is_playlist: false,
    episode: null,
    episode_number: null,
    episode_id: null,
    season_id: null,
    extractor: null,
    entries: [],
    formats: [],
  };
  info.value = initialInfo;

  function onProgress(e: ParseProgressEvent) {
    parseProgress.value = { done: e.index + 1, total: e.total };
    if (!info.value) return;
    // First progress event tells us this is in fact a playlist with N entries;
    // promote the skeleton so the playlist UI renders while remaining entries
    // are still resolving.
    if (!info.value.is_playlist && e.total > 0) {
      info.value.is_playlist = true;
    }
    // Ensure the entries array is large enough (entries may arrive in order
    // but the skeleton hasn't populated them yet — we index into it).
    const entries = info.value.entries;
    while (entries.length < e.total) {
      entries.push({
        id: `_pending_${entries.length}`,
        title: `第 ${entries.length + 1} 集`,
        duration: null,
        url: null,
        detail_status: "pending",
        detail_error: null,
        best_format_id: null,
        best_format_note: null,
        best_width: null,
        best_height: null,
        best_quality: null,
      });
    }
    // Merge enriched fields from the sidecar into the skeleton slot.
    const target = entries[e.index];
    if (e.entry.id != null) target.id = e.entry.id;
    if (e.entry.title != null) target.title = e.entry.title;
    if (e.entry.duration != null) target.duration = e.entry.duration;
    if (e.entry.detail_status != null) target.detail_status = e.entry.detail_status;
    if (e.entry.best_format_note != null) target.best_format_note = e.entry.best_format_note;
    if (e.entry.best_width != null) target.best_width = e.entry.best_width;
    if (e.entry.best_height != null) target.best_height = e.entry.best_height;
    target.detail_error = e.entry.detail_error ?? null;
    // Force reactivity by reassigning the ref.
    info.value = { ...info.value, entries: [...entries] };
  }

  try {
    const result = await parseUrl(url.value.trim(), props.cookiesBrowser, onProgress);
    info.value = result;
    // Set is_playlist — the skeleton had it false, the real result has the truth.
    info.value.is_playlist = result.is_playlist;
    parseProgress.value = null;

    if (result.is_playlist) {
      // Default-select all entries.
      selectedEntryIds.value = result.entries
        .map((e) => e.id)
        .filter((id): id is string => id != null);
    }
    message.success("解析成功");
  } catch (e: any) {
    error.value = typeof e === "string" ? e : e?.message ?? "未知错误";
    info.value = null;
  } finally {
    loading.value = false;
    parseProgress.value = null;
  }
}

function doDownload(row: ParsedFormat) {
  if (!url.value.trim()) return;
  // Prefer the human-readable format name (e.g. "4K 超高清") because the
  // bilibili extractor gives us the nice Chinese labels there.
  const prefix = row.format_note || (row.height ? `${row.height}p` : "音频");
  const label = `${prefix} · ${row.format_id}`;
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

function entryBestQualityLabel(row: ParsedEntry): string {
  if (row.best_format_note) return row.best_format_note;
  if (row.detail_status === "error") return "解析失败";
  if (row.detail_status === "no_formats") return "无可用画质";
  return "—";
}

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
  { title: "ID", key: "format_id", width: 80 },
  {
    title: "清晰度",
    key: "format_note",
    minWidth: 130,
    // The Bilibili extractor labels each stream as e.g. "1080P 高清",
    // "1080P 高码率", "4K 超高清". Those names are far more useful than the
    // raw pixel height (especially since cinematic 21:9 episodes are 1632p,
    // not 2160p — but they ARE 4K). Fall back to a "<height>p" label only
    // when no proper note exists; pure audio rows have neither.
    render: (row) =>
      row.format_note ?? (row.height ? `${row.height}p` : "音频"),
  },
  {
    title: "尺寸",
    key: "height",
    width: 100,
    render: (row) =>
      row.width && row.height ? `${row.width}×${row.height}` : "—",
  },
  { title: "容器", key: "ext", width: 70 },
  {
    title: "大小",
    key: "filesize",
    width: 100,
    render: (row) => {
      if (row.filesize == null) return "—";
      const mb = row.filesize / 1024 / 1024;
      return mb >= 1024
        ? `${(mb / 1024).toFixed(2)} GB`
        : `${mb.toFixed(0)} MB`;
    },
  },
  { title: "视频编码", key: "vcodec", width: 110 },
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
  {
    title: "最高画质",
    key: "best_format_note",
    width: 150,
    render: entryBestQualityLabel,
  },
  {
    title: "尺寸",
    key: "best_height",
    width: 110,
    render: (row) =>
      row.best_width && row.best_height ? `${row.best_width}×${row.best_height}` : "—",
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
            <h3>{{ info.display_title || info.title || "(无标题)" }}</h3>
            <p v-if="info.kind !== 'bangumi'" class="uploader">
              UP 主：<strong>{{ info.uploader || "—" }}</strong>
            </p>
            <p v-else class="uploader">
              来源：<strong>B站番剧</strong>
              <span v-if="info.season_id"> · 季 {{ info.season_id }}</span>
              <span v-if="info.episode_id"> · 集 {{ info.episode_id }}</span>
            </p>
            <p class="badges">
              <n-tag
                :type="info.kind === 'bangumi' ? 'warning' : (info.is_playlist ? 'info' : 'default')"
                size="small"
              >
                {{
                  info.kind === 'bangumi'
                    ? '番剧'
                    : info.is_playlist
                      ? '合集 / 列表'
                      : '单个视频'
                }}
              </n-tag>
              <n-tag v-if="info.duration != null" size="small">
                时长 {{ formatDuration(info.duration) }}
              </n-tag>
              <n-tag v-if="info.formats.length" size="small">{{ info.formats.length }} 种格式</n-tag>
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
              <n-tag v-if="parseProgress" type="info" size="small">
                已解析 {{ parseProgress.done }} / {{ parseProgress.total }}
              </n-tag>
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
            <p class="batch-hint">
              {{
                props.cookiesBrowser
                  ? `已使用 ${props.cookiesBrowser} Cookies 解析每集可用画质；批量下载会按所选清晰度重新确认。`
                  : '当前未使用浏览器 Cookies，账号限定清晰度可能不可用。'
              }}
            </p>
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
.batch-hint {
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  color: var(--n-text-color-3, #888);
}
</style>
