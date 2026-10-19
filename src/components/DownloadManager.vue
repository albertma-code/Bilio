<script setup lang="ts">
import { computed, h, ref } from "vue";
import { invoke } from '@tauri-apps/api/core';
import { NButton, NCheckbox, NDrawer, NDrawerContent, NDropdown, NProgress, NSelect, NTooltip, useDialog, useMessage } from "naive-ui";
import { ArrowDownToLine, Film, FolderOpen, MoreHorizontal, RefreshCw, RotateCcw, SearchX, Trash2, X, Info, ExternalLink, CircleAlert, Check, ListVideo, Pause, Play } from "@lucide/vue";
import { revealItemInDir, openPath, openUrl } from "@tauri-apps/plugin-opener";
import { cancelDownload, formatBytes, formatEta, formatSpeed, taskCommand } from "../lib/sidecar";
import { tasks, isActive, isTerminal, resumeTask, clearFinished, refreshTasks, removeFinished, type DownloadTask } from "../lib/tasks";
import { actualQuality, taskFilename, taskQuality } from "../lib/presentation";

const props = defineProps<{ view: string; search: string }>();
defineEmits<{ (event: 'new'): void }>();
const message = useMessage(), dialog = useDialog();
const detailId = ref<number | null>(null), refreshing = ref(false);
const pending = ref(new Set<number>());
const selected = ref<number[]>([]);
const sort=ref('status');
const collapsed=ref(new Set<string>());
function toggleGroup(id:string) { const next=new Set(collapsed.value);next.has(id)?next.delete(id):next.add(id);collapsed.value=next; }
function batchName(id:string) { const title=tasks.value.find(t=>t.config.batchId===id)?.config.title || '';return title.includes(' · ')?title.split(' · ').slice(0,-1).join(' · '):'分集批次'; }
function batchStatus(id:string) { const all=tasks.value.filter(t=>t.config.batchId===id);return `${all.filter(t=>t.status==='completed').length} 完成 · ${all.filter(t=>['failed','interrupted'].includes(t.status)).length} 失败 · ${all.filter(t=>['queued','paused'].includes(t.status)).length} 等待`; }
const selectedTasks = computed(() => tasks.value.filter(t => selected.value.includes(t.jobId)));
const pausable = (t: DownloadTask) => ['queued','starting','downloading'].includes(t.status);
const resumable = (t: DownloadTask) => ['paused','interrupted','failed','cancelled'].includes(t.status);
const detail = computed(() => tasks.value.find(t => t.jobId === detailId.value));
const detailOpen = computed({ get: () => !!detail.value, set: (open: boolean) => { if (!open) detailId.value = null; } });
const visible = computed(() => tasks.value.filter(task => {
  const matches = props.view === 'all' || (props.view === 'active' && isActive(task)) ||
    (props.view === 'completed' && task.status === 'completed') ||
    (props.view === 'paused' && task.status === 'paused') ||
    (props.view === 'attention' && ['failed', 'cancelled', 'interrupted'].includes(task.status));
  return matches && `${task.config.title} ${taskFilename(task)}`.toLowerCase().includes(props.search.trim().toLowerCase());
}).slice().sort((a, b) => (sort.value==='status'?Number(isActive(b)) - Number(isActive(a)):0) || b.jobId - a.jobId));
const groups = computed(() => {
  const result: Array<{ id: string; batch?: string; tasks: DownloadTask[] }> = [];
  for (const task of visible.value) {
    const id = task.config.batchId || `single-${task.jobId}`;
    let group = result.find(item => item.id === id);
    if (!group) { group = { id, batch: task.config.batchId, tasks: [] }; result.push(group); }
    group.tasks.push(task);
  }
  return result;
});
const canClear = computed(() => tasks.value.some(isTerminal));
const labels: Record<DownloadTask['status'], string> = { queued: '排队中', starting: '准备下载', downloading: '下载中', processing: '合并中', pausing:'正在暂停', paused:'已暂停', cancelling: '停止中', completed: '已完成', cancelled: '已取消', failed: '下载失败', interrupted: '已中断' };
async function action(work: () => Promise<unknown>) { try { await work(); } catch (error) { message.error(String(error)); } }
async function retry(task: DownloadTask) {
  if (pending.value.has(task.jobId)) return;
  pending.value.add(task.jobId);
  try { await action(() => resumeTask(task)); }
  finally { pending.value.delete(task.jobId); }
}
async function reveal(task: DownloadTask) {
  try { if (!task.filepath) throw new Error(); await revealItemInDir(task.filepath); }
  catch { message.warning('文件可能已移动，正在尝试打开保存目录'); await action(() => openPath(task.config.output_dir)); }
}
function confirmClear() {
  dialog.warning({ title: '清空已结束的记录？', content: '包含已完成、失败、中断及取消的任务。本地视频文件不会删除。', positiveText: '仅清空记录', negativeText: '返回', onPositiveClick: () => action(clearFinished) });
}
function remove(task: DownloadTask) {
  dialog.warning({ title: '移除此下载记录？', content: '仅移除列表记录，本地视频文件不会删除。', positiveText: '移除记录', negativeText: '返回', onPositiveClick: () => action(() => removeFinished(task)) });
}
function menuOptions(task: DownloadTask) {
  const icon = (component: typeof Info) => () => h(component, { size: 15 });
  return [ { label: '任务详情', key: 'detail', icon: icon(Info) },
    { label: '打开原视频', key: 'source', icon: icon(ExternalLink) },
    ...(!isActive(task)?[{label:'清理临时文件',key:'temporary',icon:icon(Trash2)}]:[]),
    ...(resumable(task)?[{label:'更换保存目录',key:'relocate',icon:icon(FolderOpen)}]:[]),
    ...(isTerminal(task) ? [{ type: 'divider' as const }, { label: '移除记录', key: 'remove', icon: icon(Trash2) }] : []) ];
}
function menuAction(key: string, task: DownloadTask) {
  if (key === 'detail') detailId.value = task.jobId;
  if (key === 'source') void action(() => openUrl(task.config.url));
  if (key === 'remove') remove(task);
  if (key === 'temporary') void cleanTemporary(task);
  if (key === 'relocate') void relocate(task);
}
async function relocate(task:DownloadTask) {
  const directory=await invoke<string|null>('choose_download_directory').catch(()=>null);
  if(!directory)return;
  dialog.warning({title:'更换此任务的保存目录？',content:`新目录：${directory}。继续时会重新下载；旧临时文件保留，可另行清理。不会移动或删除已有视频。`,positiveText:'更换目录',negativeText:'返回',onPositiveClick:()=>action(()=>taskCommand('relocate',{job_id:task.jobId,output_dir:directory}).then(refreshTasks))});
}
async function cleanTemporary(task:DownloadTask) {
  try {
    const summary=await taskCommand<{file_count:number;bytes:number}>('temp_info',{job_id:task.jobId});
    if(!summary.file_count){message.info('此任务没有可清理的临时文件');return;}
    dialog.warning({title:'清理此任务的临时文件？',content:`${summary.file_count} 个文件，${formatBytes(summary.bytes)}。不会删除最终视频；下次继续可能需要重新下载。`,positiveText:'清理临时文件',negativeText:'返回',onPositiveClick:()=>action(()=>taskCommand('clear_temp',{job_id:task.jobId}))});
  } catch(error) {message.error(String(error));}
}
async function refresh() { refreshing.value = true; try { await action(refreshTasks); } finally { refreshing.value = false; } }
function toggleSelection(id:number, checked:boolean) { selected.value = checked ? [...new Set([...selected.value,id])] : selected.value.filter(x=>x!==id); }
function bulk(mode:'pause'|'resume'|'remove') {
  const targets = selectedTasks.value.filter(mode === 'pause' ? pausable : mode === 'resume' ? resumable : isTerminal);
  if (!targets.length) return;
  const name = mode === 'pause' ? '暂停' : mode === 'resume' ? '继续或重试' : '移除记录';
  dialog.warning({title:`${name} ${targets.length} 个任务？`,content:mode==='remove'?'只移除已结束记录，不删除视频和临时文件。':'仅处理符合当前状态的所选任务，画质与保存目录不变。',positiveText:'确认',negativeText:'返回',onPositiveClick:async()=>{
    for(const task of targets) await action(()=>mode==='pause'?taskCommand('pause',{job_id:task.jobId}):mode==='resume'?resumeTask(task):removeFinished(task));
    await action(refreshTasks);selected.value=[];
  }});
}
function confirmCancelBatch(batchId: string) {
  dialog.warning({ title: '取消这个批次？', content: '将停止该批次中尚未完成的任务，已完成文件不受影响。', positiveText: '取消批次', negativeText: '返回', onPositiveClick: () => action(() => taskCommand('cancel_batch', { batch_id: batchId })) });
}
</script>

<template>
  <section class="task-manager" aria-label="下载列表">
    <div class="list-toolbar"><span>{{ visible.length ? `${visible.length} 个任务` : '下载列表' }}</span><div><n-select v-model:value="sort" :options="[{label:'状态优先',value:'status'},{label:'最新优先',value:'created'}]" size="small" style="width:110px" aria-label="任务排序" /><n-tooltip><template #trigger><n-button quaternary circle size="small" aria-label="刷新下载列表" :loading="refreshing" @click="refresh"><RefreshCw :size="15" /></n-button></template>刷新</n-tooltip><n-tooltip><template #trigger><n-button quaternary circle size="small" aria-label="清空已结束记录" :disabled="!canClear" @click="confirmClear"><Trash2 :size="15" /></n-button></template>清空已结束记录，不删除文件</n-tooltip></div></div>
    <div class="selection-toolbar"><n-checkbox :checked="!!visible.length && visible.every(t=>selected.includes(t.jobId))" :indeterminate="visible.some(t=>selected.includes(t.jobId)) && !visible.every(t=>selected.includes(t.jobId))" aria-label="选择当前视图任务" @update:checked="checked=>selected=checked?[...new Set([...selected,...visible.map(t=>t.jobId)])]:selected.filter(id=>!visible.some(t=>t.jobId===id))" /><span>{{ selectedTasks.length ? `已选 ${selectedTasks.length} 项` : '选择任务' }}</span><template v-if="selectedTasks.length"><n-tooltip><template #trigger><n-button quaternary circle aria-label="暂停所选任务" :disabled="!selectedTasks.some(pausable)" @click="bulk('pause')"><Pause :size="15" /></n-button></template>暂停所选任务</n-tooltip><n-tooltip><template #trigger><n-button quaternary circle aria-label="继续所选任务" :disabled="!selectedTasks.some(resumable)" @click="bulk('resume')"><Play :size="15" /></n-button></template>继续或重试所选任务</n-tooltip><n-tooltip><template #trigger><n-button quaternary circle aria-label="移除所选记录" :disabled="!selectedTasks.some(isTerminal)" @click="bulk('remove')"><Trash2 :size="15" /></n-button></template>移除所选已结束记录</n-tooltip></template></div>
    <div class="task-scroll">
      <div v-if="!visible.length" class="empty-state">
        <div class="empty-symbol"><SearchX v-if="search" :size="30" :stroke-width="1.4" /><ArrowDownToLine v-else-if="!tasks.length" :size="30" :stroke-width="1.4" /><Check v-else :size="30" :stroke-width="1.4" /></div>
        <h3>{{ search ? '没有匹配的任务' : !tasks.length ? '还没有下载任务' : view === 'attention' ? '没有需要处理的任务' : view === 'completed' ? '还没有已完成的下载' : view === 'paused' ? '当前没有暂停的下载' : '当前没有进行中的下载' }}</h3>
        <n-button v-if="!search" :secondary="true" type="primary" @click="$emit('new')"><template #icon><ArrowDownToLine :size="15" /></template>新建下载</n-button>
      </div>
      <section v-for="group in groups" :key="group.id" class="task-group">
        <div v-if="group.batch" class="batch-heading"><ListVideo :size="14" /><button class="batch-toggle" :aria-label="collapsed.has(group.id)?'展开批次':'折叠批次'" :aria-expanded="!collapsed.has(group.id)" @click="toggleGroup(group.id)">{{ batchName(group.id) }} · {{ group.tasks.length }} 项</button><span class="batch-stats">{{ batchStatus(group.id) }}</span><n-button v-if="group.tasks.some(t=>isActive(t)||t.status==='paused')" quaternary size="tiny" @click="confirmCancelBatch(group.batch!)">取消批次</n-button></div>
        <template v-if="!collapsed.has(group.id)">
        <article v-for="task in group.tasks" :key="task.jobId" class="task-row" :class="task.status" :data-task-id="task.jobId">
          <n-checkbox class="task-select" :checked="selected.includes(task.jobId)" :aria-label="`选择任务 ${task.config.title}`" @update:checked="checked=>toggleSelection(task.jobId,checked)" />
          <div class="task-symbol"><CircleAlert v-if="['failed','interrupted'].includes(task.status)" :size="20" /><Film v-else :size="20" :stroke-width="1.6" /></div>
          <div class="task-content"><div class="task-heading"><button class="task-title" @click="detailId = task.jobId">{{ task.config.title }}</button><span class="task-status">{{ labels[task.status] }}</span></div>
            <div class="task-meta"><span>{{ taskQuality(task) }}</span><span v-if="task.status === 'completed' && task.totalBytes">{{ formatBytes(task.totalBytes) }}</span><span v-if="actualQuality(task) && actualQuality(task) !== taskQuality(task)">实际 {{ actualQuality(task) }}</span></div>
            <div v-if="task.status === 'downloading' || task.status === 'processing'" class="progress-block"><n-progress type="line" :percentage="task.status === 'processing' ? 99 : task.progress ?? 0" :show-indicator="false" :processing="true" :height="4" :color="'var(--progress)'" /><div class="download-stats"><span>{{ task.status === 'processing' ? '正在合并音视频' : `${formatBytes(task.downloadedBytes)} / ${formatBytes(task.totalBytes)}` }}</span><span v-if="task.status === 'downloading'">{{ formatSpeed(task.speed) }}<span v-if="task.eta != null"> · {{ formatEta(task.eta) }}</span></span><span>{{ Math.floor(task.progress || 0) }}%</span></div></div>
            <p v-if="task.errorMessage" class="task-error">{{ task.errorMessage }}</p><p v-if="task.storageError" class="task-error">{{ task.storageError }}</p>
          </div>
          <div class="row-actions"><n-tooltip v-if="pausable(task)"><template #trigger><n-button quaternary circle aria-label="暂停下载" @click="action(() => taskCommand('pause', {job_id:task.jobId}).then(refreshTasks))"><Pause :size="16" /></n-button></template>暂停下载</n-tooltip><n-tooltip v-if="task.status === 'paused'"><template #trigger><n-button quaternary circle aria-label="继续下载" @click="retry(task)"><Play :size="16" /></n-button></template>继续下载</n-tooltip><n-tooltip v-if="task.status === 'completed'"><template #trigger><n-button quaternary circle aria-label="在 Finder 中显示" @click="reveal(task)"><FolderOpen :size="17" /></n-button></template>在 Finder 中显示</n-tooltip><n-tooltip v-if="['failed','cancelled','interrupted'].includes(task.status)"><template #trigger><n-button quaternary circle aria-label="按原配置重试" :loading="pending.has(task.jobId)" @click="retry(task)"><RotateCcw :size="16" /></n-button></template>按原配置重试</n-tooltip><n-tooltip v-if="isActive(task) || task.status === 'paused'"><template #trigger><n-button quaternary circle aria-label="取消下载" :disabled="task.status === 'cancelling'" @click="action(() => cancelDownload(task.jobId))"><X :size="17" /></n-button></template>取消下载</n-tooltip><n-dropdown trigger="click" :options="menuOptions(task)" @select="key => menuAction(key, task)"><n-button quaternary circle aria-label="更多操作"><MoreHorizontal :size="18" /></n-button></n-dropdown></div>
        </article>
        </template>
      </section>
    </div>
    <n-drawer v-model:show="detailOpen" :width="'min(440px, 100vw)'" placement="right"><n-drawer-content title="任务详情" closable><template v-if="detail"><h3 class="detail-title">{{ detail.config.title }}</h3><span class="detail-status">{{ labels[detail.status] }}</span><dl class="detail-list"><dt>下载画质</dt><dd>{{ taskQuality(detail) }}</dd><template v-if="actualQuality(detail)"><dt>实际画质</dt><dd>{{ actualQuality(detail) }}</dd></template><dt>文件名</dt><dd>{{ taskFilename(detail) || '完成后生成' }}</dd><dt>保存位置</dt><dd>{{ detail.filepath || detail.config.output_dir }}</dd><dt>文件大小</dt><dd>{{ formatBytes(detail.totalBytes) }}</dd><dt>尝试次数</dt><dd>{{ (detail.attempt || 0) + 1 }}</dd><template v-if="detail.attemptHistory?.length"><dt>此前尝试</dt><dd v-for="(attempt,index) in detail.attemptHistory" :key="index">第 {{ attempt.attempt }} 次 · {{ labels[attempt.status as DownloadTask['status']] || attempt.status }}<span v-if="attempt.errorCode"> · {{ attempt.errorCode }}</span></dd></template><dt>登录来源</dt><dd>{{ detail.config.cookies_from_browser === 'bilio' ? 'Bilio 独立登录' : detail.config.cookies_from_browser || '未使用账号' }}</dd></dl><p v-if="detail.errorMessage" class="task-error">{{ detail.errorMessage }}</p><div class="detail-actions"><n-button v-if="detail.status === 'completed'" @click="reveal(detail)"><template #icon><FolderOpen :size="16" /></template>在 Finder 中显示</n-button><n-button @click="action(() => openUrl(detail!.config.url))"><template #icon><ExternalLink :size="16" /></template>原视频</n-button></div></template></n-drawer-content></n-drawer>
  </section>
</template>

<style scoped>
.task-manager { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.selection-toolbar { display:flex; align-items:center; gap:8px; min-height:36px; font-size:11px; color:var(--secondary); border-bottom:1px solid var(--border); }
.task-select { margin-top:8px; flex-shrink:0; }
.batch-toggle { border:0; background:transparent; color:inherit; cursor:pointer; padding:0; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; text-align:left; }.batch-stats {font-size:10px; flex-shrink:0;}.batch-heading {flex-wrap:wrap;}
.list-toolbar { min-height: 34px; border-bottom: 1px solid #e5eae7; display: flex; align-items: center; justify-content: space-between; color: #737b80; font-size: 11px; }
.list-toolbar > div { display: flex; gap: 4px; }.task-scroll { overflow-y: auto; flex: 1; min-height: 0; scrollbar-gutter: stable; }
.task-row { display: flex; align-items: flex-start; gap: 14px; padding: 20px 2px; border-bottom: 1px solid #edf0ee; }
.task-symbol { width: 38px; height: 44px; flex: 0 0 38px; display: flex; align-items: center; justify-content: center; background: #f2f4f5; color: #85949e; border-radius: 4px; margin-top: 1px; }
.failed .task-symbol, .interrupted .task-symbol { background: #fcf0eb; color: #ba7557; }
.task-content { min-width: 0; flex: 1; }.task-heading { display: flex; align-items: baseline; gap: 16px; justify-content: space-between; }
.task-title { border: 0; background: transparent; color: #30343a; font-size: 13px; font-weight: 550; text-align: left; padding: 0; cursor: pointer; line-height: 1.6; overflow-wrap: anywhere; min-width: 0; }
.task-title:hover { color: #167c69; }.task-status { font-size: 11px; white-space: nowrap; color: #737c80; }.completed .task-status { color: #328269; }.downloading .task-status, .processing .task-status { color: #167c69; }.failed .task-status, .interrupted .task-status { color: #b16c4c; }
.task-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; color: #70797f; font-size: 11px; margin-top: 4px; }
.row-actions { display: flex; align-items: center; gap: 2px; padding-top: 2px; }.row-actions .n-button { width: 30px; height: 30px; color: #7e8b84; }
.progress-block { margin-top: 12px; max-width: 560px; }.download-stats { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px 12px; font-size: 10px; color: #717c82; margin-top: 6px; font-variant-numeric: tabular-nums; }
.task-error { font-size: 12px; color: #b56a51; margin: 6px 0 0; overflow-wrap: anywhere; }
.batch-heading { display: flex; align-items: center; gap: 6px; padding: 12px 0 8px; font-size: 11px; color: #6c787f; border-bottom: 1px solid #edf0ee; }.batch-heading .n-button { margin-left: auto; }
.empty-state { min-height: 220px; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 30px 10px; }.empty-symbol { width: 68px; height: 68px; display: flex; align-items: center; justify-content: center; color: #97aba0; background: #f2f6f3; border-radius: 8px; }.empty-state h3 { margin: 20px 0; font-size: 14px; font-weight: 500; color: #68757d; }
.detail-title { font-size: 17px; line-height: 1.6; margin: 0 0 10px; overflow-wrap: anywhere; }.detail-status { color: #167c69; font-size: 12px; }.detail-list { margin-top: 28px; }.detail-list dt { font-size: 11px; color: #707b80; margin-top: 20px; }.detail-list dd { margin: 5px 0 0; font-size: 13px; overflow-wrap: anywhere; }.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 28px; }
@media (max-width: 680px) { .task-row { gap: 10px; padding: 16px 0; }.task-heading { gap: 8px; }.task-symbol { width: 30px; flex-basis: 30px; height: 38px; }.row-actions { gap: 0; } }
</style>
