<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NDrawer, NDrawerContent, NInput, NTooltip, useDialog, useMessage } from "naive-ui";
import { Plus, Download, ListVideo, CircleCheck, CircleAlert, Search, FolderOpen, ChevronRight } from "@lucide/vue";
import { openPath } from "@tauri-apps/plugin-opener";
import DownloadManager from "./DownloadManager.vue";
import UrlParser from "./UrlParser.vue";
import { type CookieStatus, type QualityPolicy } from "../lib/sidecar";
import { tasks, isActive, submitTask, engineReady } from "../lib/tasks";

const props = defineProps<{ downloadDir: string; cookiesBrowser?: string; cookieStatus?: CookieStatus | null; cookieStatusLoading?: boolean; authRevision: string; concurrency?:number }>();
defineEmits<{ (event: 'settings'): void }>();
const message = useMessage(), dialog = useDialog();
const effectiveBrowser = computed(() => props.cookiesBrowser === 'bilio' && !props.cookieStatus?.logged_in &&
  props.cookieStatus?.state === 'signed_out' ? undefined : props.cookiesBrowser);
const addOpen = ref(false), submitting = ref(false), search = ref("");
const view = ref("all");
const parser = ref<InstanceType<typeof UrlParser>>();
watch(addOpen, open => { if (!open) parser.value?.cancelParse(); });
const sections = computed(() => [
  { id: 'all', label: '全部任务', icon: ListVideo, count: tasks.value.length },
  { id: 'active', label: '进行中', icon: Download, count: tasks.value.filter(isActive).length },
  { id: 'paused', label: '已暂停', icon: CircleAlert, count: tasks.value.filter(t => t.status === 'paused').length },
  { id: 'completed', label: '已完成', icon: CircleCheck, count: tasks.value.filter(t => t.status === 'completed').length },
  { id: 'attention', label: '需要处理', icon: CircleAlert, count: tasks.value.filter(t => ['failed', 'interrupted', 'cancelled'].includes(t.status)).length },
]);
const current = computed(() => sections.value.find(s => s.id === view.value)!);
const accountProblem = computed(() => !!props.cookiesBrowser && props.cookiesBrowser !== 'bilio' && !props.cookieStatusLoading &&
  !!props.cookieStatus && !props.cookieStatus.logged_in);
async function openDirectory() {
  try { await openPath(props.downloadDir); } catch { message.warning("无法打开下载目录，目录可能尚未创建"); }
}
async function handleDownload(url: string, policy: QualityPolicy, formatLabel: string, title: string, outputDir:string, conflict:'keep_both'|'reuse_verified'|'replace') {
  if (submitting.value) return;
  submitting.value = true;
  try {
    await submitTask({ url, policy, title, formatLabel, output_dir: outputDir, conflict_policy:conflict, cookies_from_browser: effectiveBrowser.value, auth_session_id:effectiveBrowser.value==='bilio'?props.authRevision:undefined });
    addOpen.value = false; view.value = 'all'; search.value = '';
    message.success("已加入下载列表");
  } catch (error) { message.error(String(error)); }
  finally { submitting.value = false; }
}
async function handleBatchDownload(entries: Array<{ url: string; title: string }>, policy: QualityPolicy, formatLabel: string, outputDir:string, conflict:'keep_both'|'reuse_verified'|'replace') {
  if (!entries.length || submitting.value) return;
  const revision = props.authRevision;
  const context = { output_dir: outputDir, conflict_policy:conflict, cookies_from_browser: effectiveBrowser.value, auth_session_id:effectiveBrowser.value==='bilio'?revision:undefined };
  const confirmed = await new Promise<boolean>(resolve => {
    dialog.warning({ title: `添加 ${entries.length} 个分集`,
      content: `画质：${formatLabel}。保存到：${context.output_dir}。${conflict==='replace'?'新文件验证成功后替换同名文件，旧文件无法恢复。':conflict==='reuse_verified'?'内容完全相同才复用，否则保留两份。':'同名文件保留两份，不覆盖已有视频。'}`,
      positiveText: '加入下载列表', negativeText: '返回',
      onPositiveClick: () => resolve(true), onNegativeClick: () => resolve(false), onClose: () => resolve(false), onMaskClick: () => resolve(false), onEsc: () => resolve(false) });
  });
  if (!confirmed) return;
  if (revision !== props.authRevision || context.cookies_from_browser !== effectiveBrowser.value) {
    message.warning("设置已变化，请重新确认"); return;
  }
  submitting.value = true;
  const batchId = crypto.randomUUID();
  let success = 0, failed = 0;
  addOpen.value = false; view.value = 'all'; search.value = '';
  try {
    for (const entry of entries) {
      if (revision !== props.authRevision || context.cookies_from_browser !== effectiveBrowser.value) { failed++;break; }
      try { await submitTask({ ...entry, policy, formatLabel, batchId, ...context }); success++; }
      catch (error) { failed++; if (String(error).includes('本批次已取消')) break; }
    }
    if (failed) message.warning(`${success} 个已加入，${entries.length-success} 个未能提交`);
    else message.success(`${success} 个分集已加入下载列表`);
  } finally { submitting.value = false; }
}
</script>

<template>
  <div class="workspace">
    <aside class="sidebar">
      <n-button type="primary" class="sidebar-add" :disabled="!engineReady || submitting" @click="addOpen = true"><template #icon><Plus :size="17" /></template>新建下载</n-button>
      <span class="nav-caption">下载库</span>
      <nav aria-label="下载视图"><button v-for="section in sections" :key="section.id" :class="['nav-item', { selected: view === section.id }]" :aria-current="view === section.id ? 'page' : undefined" @click="view = section.id"><component :is="section.icon" :size="17" /><span>{{ section.label }}</span><span class="nav-count">{{ section.count }}</span></button></nav>
      <div class="sidebar-bottom"><span class="engine-dot" :class="{ ready: engineReady }"></span><span>{{ engineReady ? '引擎就绪' : '连接引擎中' }}</span><span class="parallel-count">并行 {{ concurrency || 2 }}</span></div>
    </aside>
    <main class="library">
      <div class="library-heading"><div><h2>{{ current.label }}</h2><span class="library-count">{{ current.count }} 个任务</span></div><n-button type="primary" class="compact-add" :disabled="!engineReady || submitting" @click="addOpen = true"><template #icon><Plus :size="16" /></template>新建下载</n-button></div>
      <div v-if="accountProblem" class="account-warning"><CircleAlert :size="16" /><span>{{ cookieStatus?.ok === false ? '无法读取浏览器登录状态' : '浏览器尚未登录 B站，会员画质可能不可用' }}</span><button @click="$emit('settings')">检查账号<ChevronRight :size="14" /></button></div>
      <div class="library-tools"><n-input v-model:value="search" clearable placeholder="搜索任务名称" aria-label="搜索任务名称"><template #prefix><Search :size="15" color="#899394" /></template></n-input><n-tooltip><template #trigger><n-button quaternary circle aria-label="打开下载文件夹" @click="openDirectory"><FolderOpen :size="18" /></n-button></template>打开下载文件夹</n-tooltip></div>
      <DownloadManager :view="view" :search="search" @new="addOpen = true" />
      <footer class="library-footer"><FolderOpen :size="13" /><button :title="downloadDir" @click="$emit('settings')">{{ downloadDir }}</button><span>仅保存你有权下载的内容</span></footer>
    </main>
    <n-drawer v-model:show="addOpen" :width="'min(800px, 100vw)'" placement="right" display-directive="show" :mask-closable="!submitting" :close-on-esc="!submitting">
      <n-drawer-content title="新建下载" :closable="!submitting" :body-style="{ overflow: 'hidden' }" :body-content-style="{ height: '100%', padding: '20px 24px 0', boxSizing: 'border-box' }">
        <UrlParser ref="parser" :cookies-browser="effectiveBrowser" :download-dir="downloadDir" :busy="submitting" :auth-revision="authRevision" @settings="$emit('settings')" @download="handleDownload" @batch-download="handleBatchDownload" />
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.workspace { display: flex; flex: 1; min-height: 0; }
.sidebar { width: 192px; flex: 0 0 192px; background: #f7f7f8; border-right: 1px solid #e7eae8; padding: 24px 14px 14px; display: flex; flex-direction: column; }
.sidebar-add { width: 100%; }
.nav-caption { font-size: 11px; color: #747b80; margin: 30px 12px 10px; }
nav { display: flex; flex-direction: column; gap: 4px; }
.nav-item { border: 0; background: transparent; color: #626b72; width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; min-height: 40px; padding: 9px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.nav-item:hover { background: #edf1ee; }
.nav-item.selected { color: #136a59; background: #e3efea; font-weight: 600; }
.nav-count { margin-left: auto; font-size: 11px; font-variant-numeric: tabular-nums; color: #707a80; }
.sidebar-bottom { margin-top: auto; display: flex; align-items: center; gap: 6px; font-size: 10px; color: #737c80; padding: 16px 5px 0; }
.engine-dot { width: 5px; height: 5px; background: #c59c3b; border-radius: 50%; }
.engine-dot.ready { background: #4c9c7e; }
.parallel-count { margin-left: auto; }
.library { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 28px 30px 0; }
.library-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.library-heading > div { display: flex; align-items: baseline; gap: 12px; }
h2 { font-size: 20px; font-weight: 600; margin: 0; }
.library-count { font-size: 12px; color: #737b80; white-space: nowrap; }
.compact-add { display: none; }
.library-tools { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.library-tools .n-input { max-width: 300px; }
.library-tools > .n-button { margin-left: auto; }
.library-footer { display: flex; align-items: center; gap: 6px; min-height: 42px; font-size: 10px; color: #929b99; border-top: 1px solid #e9edeb; }
.library-footer button { background: transparent; border: 0; color: #7f8b86; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 55%; font-size: 10px; padding: 0; }
.library-footer > span { margin-left: auto; white-space: nowrap; }
.account-warning { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #fff7e9; color: #926c25; font-size: 12px; margin-bottom: 16px; border-radius: 4px; }
.account-warning > svg { flex-shrink: 0; }
.account-warning > span { flex: 1; }
.account-warning button { border: 0; padding: 0; background: transparent; color: #926c25; display: flex; align-items: center; cursor: pointer; white-space: nowrap; }
@media (max-width: 760px) {
  .workspace { flex-direction: column; }.sidebar { width: 100%; flex: 0 0 auto; padding: 8px 16px; border-right: 0; border-bottom: 1px solid #e7eae8; background: #fafafb; }
  .sidebar-add, .nav-caption, .sidebar-bottom { display: none; } nav { flex-direction: row; gap: 4px; }.nav-item { justify-content: center; padding: 8px; gap: 6px; min-height: 36px; }.nav-count { margin-left: 2px; }
  .library { padding: 20px 20px 0; }.compact-add { display: inline-flex; }.library-heading { margin-bottom: 16px; }
}
@media (max-width: 500px) { .nav-item { font-size: 11px; }.nav-item svg { display: none; }.library { padding: 16px 12px 0; }.library-footer > span { display: none; } }
</style>
