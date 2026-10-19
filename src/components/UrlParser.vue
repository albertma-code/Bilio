<script setup lang="ts">
import { computed, h, onUnmounted, ref, watch } from "vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import { invoke } from '@tauri-apps/api/core';
import { ArrowRight, Check, RefreshCw, Download, ExternalLink, FolderOpen, Link2, LoaderCircle, SlidersHorizontal, Video } from "@lucide/vue";
import { codecName, qualityName } from "../lib/presentation";
import {
  NAlert,
  NButton,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NDataTable,
  NEmpty,
  NImage,
  NInput,
  NSelect,
  useMessage,
  useDialog,
  type DataTableColumns,
} from "naive-ui";
import {
  formatDuration,
  formatBytes,
  parseUrl,
  type ParseProgressEvent,
  type ParsedEntry,
  type ParsedFormat,
  type ParsedVideo,
  type QualityPolicy,
} from "../lib/sidecar";

const props = defineProps<{ cookiesBrowser?: string; downloadDir: string; busy: boolean; authRevision: string }>();

const url = ref("");
const targetDir = ref(props.downloadDir);
const conflictPolicy=ref<'keep_both'|'reuse_verified'|'replace'>('keep_both');
const dialog=useDialog();
watch(()=>props.downloadDir,(value,old)=>{if(targetDir.value===old)targetDir.value=value;});
const candidates = computed(()=>Array.from(new Set(url.value.match(/https?:\/\/[^\s<>"，。！）)]+/g)||[])).slice(0,20).map(value=>({label:value,value})));
async function chooseDirectory() {
  try { const directory=await invoke<string|null>('choose_download_directory');if(directory)targetDir.value=directory; }
  catch { message.error('无法打开目录选择窗口，请重试'); }
}
const episodeSearch=ref(''), range=ref('');
const filteredEntries=computed(()=>(info.value?.entries||[]).filter(e=>(e.title||'').toLowerCase().includes(episodeSearch.value.trim().toLowerCase())));
function selectRange() {
  if(!info.value || !/^\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)*$/.test(range.value.trim())) {message.warning('请输入分集范围，例如 1-3,5');return;}
  const positions=new Set<number>();
  for(const part of range.value.split(',')) {
    const [start,end=start]=part.trim().split('-').map(Number);
    if(start<1 || end<start || end>info.value.entries.length) {message.warning('分集范围超出列表');return;}
    for(let i=start;i<=end;i++)positions.add(i-1);
  }
  selectedEntryIds.value=info.value.entries.filter((e,i)=>positions.has(i)&&e.detail_status==='ok'&&e.url).map(e=>e.id).filter((id):id is string=>!!id);
}
const loading = ref(false);
const error = ref<string | null>(null);
const info = ref<ParsedVideo | null>(null);
const selectedQuality = ref<string | null>(null);
const selectedCodec = ref('H.264');
const videoFormats = computed(() => (info.value?.formats ?? [])
  .filter((format) => format.format_id && format.vcodec && format.vcodec !== "none")
  .slice().sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0) || (b.height ?? 0) - (a.height ?? 0)));
const qualityOptions = computed(() => Array.from(new Set(videoFormats.value.map(qualityName))).map(name => ({ label: name, value: name })));
const codecOptions = computed(() => Array.from(new Set(videoFormats.value.filter(f => qualityName(f) === selectedQuality.value).map(f => codecName(f.vcodec)))).map(name => ({ label: name === 'H.264' ? 'H.264 · 兼容性优先' : name === 'HEVC' ? 'HEVC · 更小体积' : name, value: name })));
watch(codecOptions, options => { if (!options.some(o => o.value === selectedCodec.value)) selectedCodec.value = options[0]?.value || 'H.264'; });
const selectedFormat = computed(() => videoFormats.value.find(f => qualityName(f) === selectedQuality.value && codecName(f.vcodec) === selectedCodec.value));
const selectedFormatId = computed(() => selectedFormat.value?.format_id || null);
const sizeSummary = computed(() => {
  const format = selectedFormat.value;
  if (!format?.filesize) return '大小未知';
  if (format.acodec && format.acodec !== 'none') return `预计 ${formatBytes(format.filesize)}`;
  const audio = (info.value?.formats || []).filter(f => f.vcodec === 'none' && f.acodec && f.acodec !== 'none');
  if (!audio.length || audio.some(f => !f.filesize)) return `视频约 ${formatBytes(format.filesize)}，音频另计`;
  return `预计约 ${formatBytes(format.filesize + Math.max(...audio.map(f => f.filesize!)))}`;
});
function downloadSelected() {
  if (props.busy) return;
  const format = videoFormats.value.find((row) => row.format_id === selectedFormatId.value);
  if (format) doDownload(format);
}
const parsedSource = ref("");
const parsedBrowser = ref<string | undefined>();
const parsedRevision = ref('');
const stale = computed(() => !!info.value && (url.value.trim() !== parsedSource.value || props.cookiesBrowser !== parsedBrowser.value || props.authRevision !== parsedRevision.value));
let controller: AbortController | undefined;
watch(() => props.cookiesBrowser, () => { controller?.abort(); });
watch(() => props.authRevision, () => { controller?.abort(); });
onUnmounted(() => controller?.abort());
const message = useMessage();
async function openSource(source: string) {
  try { await openUrl(source); } catch { message.error("无法打开原视频链接"); }
}

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
const batchQuality = ref<string>("80");
const batchQualityOptions = [
  { label: "最高可用清晰度", value: "best" },
  { label: "4K 及以下", value: "120" },
  { label: "1080P 高清及以下", value: "80" },
  { label: "720P 及以下", value: "64" },
  { label: "480P 及以下", value: "32" },
  { label: "360P 及以下", value: "16" },
];

const canSubmit = computed(() => url.value.trim().length > 0 && !loading.value && !props.busy);

const emit = defineEmits<{
  (e: "settings"): void;
  (e: "download", url: string, policy: QualityPolicy, formatLabel: string, title: string, outputDir:string, conflict:'keep_both'|'reuse_verified'|'replace'): void;
  (
    e: "batch-download",
    entries: Array<{ url: string; title: string }>,
    policy: QualityPolicy,
    formatLabel: string,
    outputDir:string,
    conflict:'keep_both'|'reuse_verified'|'replace',
  ): void;
}>();

async function onParse() {
  if (!canSubmit.value) return;
  loading.value = true;
  error.value = null;
  info.value = null;
  selectedEntryIds.value = [];
  selectedQuality.value = null;
  parseProgress.value = null;
  controller = new AbortController();
  const currentController = controller;
  parsedSource.value = url.value.trim();
  parsedBrowser.value = props.cookiesBrowser;
  parsedRevision.value = props.authRevision;

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
    if (e.entry.url != null) target.url = e.entry.url;
    if (e.entry.detail_status != null) target.detail_status = e.entry.detail_status;
    if (e.entry.best_format_note != null) target.best_format_note = e.entry.best_format_note;
    if (e.entry.best_width != null) target.best_width = e.entry.best_width;
    if (e.entry.best_height != null) target.best_height = e.entry.best_height;
    target.detail_error = e.entry.detail_error ?? null;
    // Force reactivity by reassigning the ref.
    info.value = { ...info.value, entries: [...entries] };
  }

  try {
    const result = await parseUrl(parsedSource.value, parsedBrowser.value, onProgress, currentController.signal);
    info.value = result;
    const recommended = videoFormats.value.find(f => /1080P 高清/i.test(qualityName(f)))
      || videoFormats.value.find(f => f.height != null && f.height <= 1080) || videoFormats.value[0];
    selectedQuality.value = recommended ? qualityName(recommended) : null;
    selectedCodec.value = 'H.264';
    // Set is_playlist — the skeleton had it false, the real result has the truth.
    info.value.is_playlist = result.is_playlist;
    parseProgress.value = null;

    if (result.is_playlist) {
      // Default-select all entries.
      selectedEntryIds.value = result.entries
        .filter((entry) => !!entry.url && entry.detail_status === "ok")
        .map((e) => e.id)
        .filter((id): id is string => id != null);
    }
  } catch (e: any) {
    error.value = currentController.signal.aborted ? null : typeof e === "string" ? e : e?.message ?? "未知错误";
    info.value = null;
  } finally {
    loading.value = false;
    parseProgress.value = null;
  }
}

function doDownload(row: ParsedFormat) {
  if (!info.value || stale.value || loading.value || props.busy || !row.format_id) return;
  // Prefer the human-readable format name (e.g. "4K 超高清") because the
  // bilibili extractor gives us the nice Chinese labels there.
  const prefix = row.format_note || (row.height ? `${row.height}p` : "音频");
  const label = `${prefix} · ${codecName(row.vcodec)}`;
  const source=info.value.webpage_url || parsedSource.value,title=info.value.display_title || info.value.title || '未命名视频';
  const directory=targetDir.value,conflict=conflictPolicy.value;
  const submit=()=>{
    if(stale.value||props.busy||loading.value){message.warning('内容或账号已变化，请重新确认');return;}
    emit('download',source,{mode:'exact',format_id:row.format_id!},label,title,directory,conflict);
  };
  if(conflict==='replace')dialog.warning({title:'允许替换同名视频？',content:`目录：${directory}。仅在新文件下载并验证成功后替换同名文件；旧文件无法恢复。`,positiveText:'允许替换并加入',negativeText:'返回',onPositiveClick:submit});
  else submit();
}

function toggleEntry(id: string | null) {
  if (id == null) return;
  const i = selectedEntryIds.value.indexOf(id);
  if (i >= 0) selectedEntryIds.value.splice(i, 1);
  else selectedEntryIds.value.push(id);
}

function toggleAll() {
  if (!info.value) return;
  if (allSelected.value) {
    selectedEntryIds.value = [];
  } else {
      selectedEntryIds.value = info.value.entries.filter((e) => e.url && e.detail_status === "ok")
        .map((e) => e.id).filter((x): x is string => x != null);
  }
}

const allSelected = computed(
  () => !!info.value && info.value.entries.some((e) => e.url && e.detail_status === "ok") &&
    info.value.entries.filter((e) => e.url && e.detail_status === "ok").every((e) => selectedEntryIds.value.includes(e.id)),
);

function entryBestQualityLabel(row: ParsedEntry): string {
  if (row.best_format_note) return row.best_format_note;
  if (row.detail_status === "error") return "解析失败";
  if (row.detail_status === "no_formats") return "无可用画质";
  return "—";
}

function doBatchDownload() {
  if (!info.value || stale.value || loading.value || props.busy) return;
  const selected = info.value.entries.filter(
    (e) => e.id != null && selectedEntryIds.value.includes(e.id) && !!e.url && e.detail_status === "ok",
  );
  if (!selected.length) {
    message.warning("请先选择要下载的分集");
    return;
  }
  // Never replace a missing episode URL with the season URL.
  const items = selected
    .map((e) => ({ url: e.url!, title: `${info.value!.display_title || info.value!.title || '分集'} · ${e.title || '未命名'}` }))
    .filter((x) => x.url);
  const qualityLabel =
    batchQualityOptions.find((o) => o.value === batchQuality.value)?.label ?? batchQuality.value;
  const policy: QualityPolicy = batchQuality.value === "best" ? { mode: "best" }
    : { mode: "max_quality", quality: Number(batchQuality.value) };
  emit("batch-download", items, policy, qualityLabel, targetDir.value, conflictPolicy.value);
}

async function retryFailedEntries() {
  if(!info.value || loading.value || stale.value || props.busy)return;
  controller=new AbortController();const current=controller;loading.value=true;
  const failed=info.value.entries.filter(e=>e.url&&e.detail_status!=='ok');
  try {
    for(const entry of failed) {
      if(current.signal.aborted)break;
      try {
        const result=await parseUrl(entry.url!,props.cookiesBrowser,undefined,current.signal);
        const best=result.formats.filter(f=>f.vcodec&&f.vcodec!=='none').sort((a,b)=>(b.quality||0)-(a.quality||0))[0];
        Object.assign(entry,{duration:result.duration,detail_status:best?'ok':'no_formats',detail_error:null,best_format_note:best?.format_note,best_width:best?.width,best_height:best?.height});
      } catch { if(current.signal.aborted)break;entry.detail_status='error'; }
      info.value={...info.value!,entries:[...info.value!.entries]};
    }
  } finally {loading.value=false;}
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
        ? h(NButton, { size: "tiny", type: "primary", disabled: stale.value || loading.value, onClick: () => doDownload(row) }, () => "下载")
        : null,
  },
];

const entryColumns = computed<DataTableColumns<ParsedEntry>>(() => [
  {
    title: () =>
      h(NCheckbox, {
        checked: allSelected.value,
        disabled: loading.value || stale.value || props.busy,
        indeterminate: selectedEntryIds.value.length > 0 && !allSelected.value,
        'aria-label': '选择全部分集',
        onUpdateChecked: toggleAll,
      }),
    key: "_select",
    width: 50,
    render: (row) =>
      h(NCheckbox, {
        checked: row.id != null && selectedEntryIds.value.includes(row.id),
        disabled: !row.url || row.detail_status !== "ok" || loading.value || stale.value || props.busy,
        'aria-label': `选择${row.title || '分集'}`,
        onUpdateChecked: () => toggleEntry(row.id),
      }),
  },
  { title: "#", key: "_idx", width: 50, render: (_row, idx) => idx + 1 },
  { title: "标题", key: "title", minWidth: 150 },
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
function cancelParse() { controller?.abort(); }
defineExpose({ cancelParse });
</script>

<template>
  <div class="new-download">
    <label class="field-label" for="video-link">视频链接</label>
    <div class="input-row">
      <n-input id="video-link" v-model:value="url" placeholder="粘贴 B站视频、番剧或合集链接" clearable :disabled="loading || busy" @keydown.enter="onParse"><template #prefix><Link2 :size="16" color="#98a29b" /></template></n-input>
      <n-button v-if="!loading" type="primary" :disabled="!canSubmit" @click="onParse">解析<template #icon><ArrowRight :size="16" /></template></n-button>
      <n-button v-else @click="cancelParse">取消解析</n-button>
    </div>
    <n-alert v-if="error" type="error" class="parse-alert" role="alert">{{ error }}</n-alert>
    <n-select v-if="candidates.length>1" :options="candidates" placeholder="选择要解析的链接" aria-label="分享文本中的链接" @update:value="value=>url=value" />
    <n-alert v-if="stale && !loading" type="warning" class="parse-alert">链接或账号来源已改变，请重新解析后下载。</n-alert>
    <div v-if="loading" class="parse-loading" role="status"><LoaderCircle :size="20" class="spinning" /><div><strong>{{ parseProgress ? '正在确认分集画质' : '正在获取视频信息' }}</strong><span v-if="parseProgress">已解析 {{ parseProgress.done }} / {{ parseProgress.total }}</span></div></div>
    <div v-if="!info && !loading && !error" class="parse-empty"><Video :size="40" :stroke-width="1.2" /><span>Bilibili</span></div>
    <div v-if="info && (!loading || info.entries.length)" class="result" :class="{ stale }">
      <div v-if="!loading" class="video-summary">
        <n-image v-if="info.thumbnail" :src="info.thumbnail" :width="100" :height="112" object-fit="cover" class="thumb" preview-disabled />
        <div v-else class="cover-placeholder"><Video :size="24" /></div>
        <div class="video-meta"><span class="content-kind">{{ info.kind === 'bangumi' ? 'B站番剧' : info.is_playlist ? '合集 / 分集' : 'B站视频' }}</span><h3>{{ info.display_title || info.title || '未命名视频' }}</h3><p><span v-if="info.uploader">{{ info.uploader }} · </span><span v-if="info.duration != null">{{ formatDuration(info.duration) }}</span><span v-if="info.is_playlist">{{ info.entries.length }} 个分集</span></p><button v-if="info.webpage_url" class="source-link" @click="openSource(info.webpage_url)"><ExternalLink :size="12" />查看原视频</button></div>
      </div>
      <section v-if="info.is_playlist && info.entries.length" class="episodes-section">
        <div class="section-label"><h4>选择分集</h4><span>已选 {{ selectedEntryIds.length }} / {{ info.entries.length }}</span></div>
        <div class="episode-tools"><n-input v-model:value="episodeSearch" placeholder="搜索分集" :input-props="{'aria-label':'搜索分集'}" clearable /><n-input v-model:value="range" placeholder="1-3,5" :input-props="{'aria-label':'分集范围'}" :disabled="loading||stale||busy" /><n-button :disabled="loading||stale||busy" aria-label="应用分集范围" @click="selectRange"><template #icon><Check :size="15" /></template>选择</n-button></div>
        <n-data-table :columns="entryColumns" :data="filteredEntries" :row-key="(r: ParsedEntry) => r.id ?? ''" :max-height="260" :scroll-x="600" size="small" :bordered="false" />
        <div v-if="!loading && info.entries.some(e => e.detail_status !== 'ok')" class="partial-warning"><p>部分分集未取得可用画质，未自动选中。</p><n-button size="small" :disabled="stale||busy" @click="retryFailedEntries"><template #icon><RefreshCw :size="14" /></template>重试失败分集</n-button></div>
      </section>
      <section v-if="!loading && (videoFormats.length || info.entries.length)" class="download-options">
        <h4>下载设置</h4>
        <div class="option-row"><label>画质</label><n-select v-if="info.is_playlist" v-model:value="batchQuality" :options="batchQualityOptions" :disabled="stale || busy" aria-label="批量下载画质" /><n-select v-else v-model:value="selectedQuality" :options="qualityOptions" :disabled="stale || busy" aria-label="下载画质" /><span v-if="!info.is_playlist" class="size-summary">{{ sizeSummary }}</span></div>
        <div class="option-row"><label>保存到</label><button class="destination" :title="targetDir" :disabled="busy" @click="chooseDirectory"><FolderOpen :size="15" /><span>{{ targetDir }}</span></button></div>
        <div class="option-row"><label>同名文件</label><n-select v-model:value="conflictPolicy" :disabled="stale||busy" :options="[{label:'保留两份（推荐）',value:'keep_both'},{label:'复用内容完全相同的文件，否则保留两份',value:'reuse_verified'},{label:'验证新文件后替换同名文件',value:'replace'}]" aria-label="同名文件处理" /></div>
        <p v-if="info.is_playlist" class="batch-note">按 B站画质档位限制；各集大小在下载时确认。</p>
        <n-collapse v-if="!info.is_playlist && videoFormats.length" class="advanced-options"><n-collapse-item title="高级选项" name="advanced"><template #header-extra><SlidersHorizontal :size="14" /></template><div class="option-row"><label>视频编码</label><n-select v-model:value="selectedCodec" :options="codecOptions" :disabled="stale || busy" aria-label="视频编码" /></div><n-collapse class="format-collapse"><n-collapse-item title="技术格式列表" name="formats"><n-data-table :columns="formatColumns" :data="info.formats" :row-key="(r: ParsedFormat) => r.format_id ?? ''" :max-height="240" :scroll-x="700" size="small" /></n-collapse-item></n-collapse></n-collapse-item></n-collapse>
      </section>
      <n-empty v-if="!loading && !info.formats.length && !info.entries.length" description="未取得可下载内容" class="result-empty" />
    </div>
    <div v-if="info && !loading && (videoFormats.length || info.entries.length)" class="submit-bar"><span>{{ info.is_playlist ? `已选 ${selectedEntryIds.length} 个分集` : `${selectedQuality || ''} · ${selectedCodec}` }}</span><n-button type="primary" :loading="busy" :disabled="stale || busy || (info.is_playlist ? !selectedEntryIds.length : !selectedFormatId)" @click="info.is_playlist ? doBatchDownload() : downloadSelected()"><template #icon><Download :size="16" /></template>加入下载列表<template v-if="info.is_playlist"> ({{ selectedEntryIds.length }})</template></n-button></div>
  </div>
</template>

<style scoped>
.new-download { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.episode-tools { display:flex; gap:8px; margin-bottom:12px; align-items:center; }.episode-tools .n-input {min-width:0;flex:1;}.episode-tools .n-input:nth-child(2) {max-width:120px;}
.field-label { display: block; font-size: 12px; color: #6e7a82; margin: 0 0 8px; flex-shrink: 0; }
.input-row { display: flex; gap: 8px; flex-shrink: 0; }.input-row .n-input { flex: 1; min-width: 0; }.parse-alert { margin-top: 16px; flex-shrink: 0; }
.parse-loading { display: flex; align-items: center; gap: 12px; margin: 28px 0; color: #508575; padding: 18px 0; border-bottom: 1px solid #e9eeeb; }.parse-loading > div { display: flex; flex-direction: column; gap: 4px; }.parse-loading strong { font-size: 13px; font-weight: 500; }.parse-loading span { font-size: 12px; color: #939e97; }
.spinning { animation: spin 1.2s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
.parse-empty { min-height: 120px; flex: 1; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 18px; color: #c0ccc4; }.parse-empty span { font-size: 13px; color: #b3c0b6; }
.result { margin-top: 24px; min-height: 0; flex: 1; overflow-y: auto; padding-right: 4px; }.video-summary { display: flex; gap: 18px; padding-bottom: 26px; border-bottom: 1px solid #e8ede9; }.thumb { flex-shrink: 0; border-radius: 4px; overflow: hidden; }.cover-placeholder { width: 100px; height: 90px; flex: 0 0 100px; background: #f2f4f5; color: #a0b1a6; display: flex; align-items: center; justify-content: center; border-radius: 4px; }
.video-meta { min-width: 0; }.content-kind { color: #727c81; font-size: 11px; }.video-meta h3 { margin: 6px 0; font-size: 17px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }.video-meta p { color: #707c82; margin: 6px 0; font-size: 12px; }.source-link { display: inline-flex; align-items: center; gap: 5px; border: 0; padding: 0; background: transparent; color: #4b8571; cursor: pointer; font-size: 11px; }
.episodes-section { padding: 22px 0 0; }.section-label { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }.section-label > span { font-size: 11px; color: #737c80; }h4 { font-size: 13px; font-weight: 600; margin: 0; }
.download-options { padding-top: 24px; }.download-options > h4 { margin-bottom: 18px; }.option-row { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; min-width: 0; }.option-row > label { font-size: 12px; color: #6c787f; width: 62px; flex: 0 0 62px; }.option-row .n-select { max-width: 260px; flex: 1; min-width: 0; }.size-summary { font-size: 11px; color: #707b80; flex: 1; }
.destination { display: flex; align-items: center; gap: 6px; min-width: 0; padding: 0; border: 0; background: transparent; color: #718579; cursor: pointer; font-size: 12px; }.destination svg { flex-shrink: 0; }.destination span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.advanced-options { padding: 14px 0; border-top: 1px solid #edf0ed; font-size: 12px; }.advanced-options :deep(.n-collapse-item__header-main) { color: #7b8b7f; font-size: 12px; }.format-collapse { margin-top: 18px; }
.submit-bar { display: flex; flex-shrink: 0; align-items: center; justify-content: space-between; gap: 12px; padding: 18px 0; background: #fff; border-top: 1px solid #e8ede9; z-index: 2; }.submit-bar > span { font-size: 12px; color: #707b80; min-width: 0; overflow-wrap: anywhere; }.partial-warning { color: #a7793c; font-size: 12px; }.batch-note { color: #707b80; font-size: 11px; margin: -4px 0 20px; }.result-empty { padding: 40px 0; }
@media (max-width: 580px) { .option-row { flex-wrap: wrap; gap: 8px; }.size-summary { margin-left: 70px; flex-basis: 100%; }.video-summary { gap: 12px; }.video-meta h3 { font-size: 15px; }.submit-bar { align-items: flex-start; }.submit-bar .n-button { flex-shrink: 0; } }
</style>
