<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from '@tauri-apps/plugin-opener';
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { NButton, NConfigProvider, NDialogProvider, NDrawer, NDrawerContent, NInput,
  NMessageProvider, NSelect, NTooltip, NCollapse, NCollapseItem, NCheckbox, NModal, NPopconfirm, zhCN, dateZhCN } from "naive-ui";
import { Settings2, FolderOpen, RefreshCw, CircleUserRound, ShieldCheck, Copy, Download } from "@lucide/vue";
import MainArea from "./components/MainArea.vue";
import { checkCookies, type CookieStatus } from "./lib/sidecar";
import { engineReady, engineError, historyWarning, initializeTasks, restartEngine, tasks, isActive } from "./lib/tasks";
import { taskCommand } from './lib/sidecar';
import appIcon from "../src-tauri/icons/128x128.png";
import { theme } from './lib/theme';
import './styles/theme.css';

const settingsOpen = ref(false);
const exitConfirm = ref(false);
const updateBusy=ref(false), updateMessage=ref(''), updateUrl=ref('');
async function checkUpdate() {
  updateBusy.value=true;updateUrl.value='';
  try {
    const result=await invoke<{status:string;version?:string;url?:string}>('check_update');
    updateMessage.value=result.status==='available'?`公开版本 ${result.version} 可用`:result.status==='current'?'与公开版本一致':result.status==='local_ahead'?'本机候选版高于当前公开版本':result.status==='no_release'?'尚无公开正式版本':'无法连接公开版本信息，请稍后重试';
    if(result.status==='available'&&result.url)updateUrl.value=result.url;
  } catch {updateMessage.value='检查失败，请确认网络与引擎状态';}
  finally {updateBusy.value=false;}
}
const diagnosticOpen=ref(false), diagnosticText=ref(''), diagnosticError=ref('');
async function showDiagnostics() {
  diagnosticError.value='';
  try {diagnosticText.value=JSON.stringify(await invoke('diagnostic_report'),null,2);diagnosticOpen.value=true;}
  catch {authError.value='无法生成诊断，请重启应用后重试';}
}
async function copyDiagnostics() {
  try {await navigator.clipboard.writeText(diagnosticText.value);diagnosticError.value='已复制脱敏诊断';}
  catch {diagnosticError.value='无法复制，请使用导出诊断';}
}
async function exportDiagnostics() {
  try {if(await invoke('export_diagnostics'))diagnosticError.value='已导出脱敏诊断';}
  catch {diagnosticError.value='导出失败，请检查目录权限和空间';}
}
const concurrency = ref(Number(localStorage.getItem('bilio-concurrency')) || 2);
async function saveConcurrency(value:number) {
  try { await taskCommand('set_concurrency',{concurrency:value});concurrency.value=value;localStorage.setItem('bilio-concurrency',String(value)); }
  catch(error) { authError.value=String(error); }
}
const downloadDir = ref(localStorage.getItem("bilio-download-dir") ?? "~/Downloads/Bilio");
const independentDefault = localStorage.getItem("bilio-auth-mode-v1") !== '1';
const cookiesBrowser = ref<string | null>(independentDefault ? 'bilio' : localStorage.getItem("bilio-cookies-browser") || null);
if (independentDefault) {
  localStorage.setItem("bilio-auth-mode-v1", '1');
  localStorage.setItem("bilio-cookies-browser", 'bilio');
}
interface AuthStatus extends CookieStatus { session_id: string | null; login_open: boolean; persistence: string; state?: string }
const authStatus = ref<AuthStatus | null>(null);
const rememberConfirm = ref(false), persistenceBusy = ref(false);
let rememberRevision = '';
const loginBusy = ref(false);
const authError = ref('');
let unlistenAuth: UnlistenFn | undefined;
let unlistenQuit: UnlistenFn | undefined;
function applyAuth(status: AuthStatus) {
  authStatus.value = status;
  if (cookiesBrowser.value === 'bilio') {
    ++probeVersion; cookieStatusLoading.value = false;
    cookieStatus.value = status;
  }
}
async function login() {
  if (loginBusy.value) return;
  loginBusy.value = true; authError.value = '';
  saveCookiesBrowser('bilio');
  try { applyAuth(await invoke<AuthStatus>('auth_login')); }
  catch (error) { authError.value = String(error); settingsOpen.value = true; }
  finally { loginBusy.value = false; }
}
async function logout() {
  authError.value = '';
  try { applyAuth(await invoke<AuthStatus>('auth_logout')); }
  catch (error) { authError.value = String(error); }
}
function changeRemember(checked: boolean) {
  if (checked) { rememberRevision = authStatus.value?.session_id || ''; rememberConfirm.value = true; }
  else void setRemember(false, authStatus.value?.session_id || '');
}
async function setRemember(remember: boolean, sessionId: string) {
  persistenceBusy.value = true; authError.value = '';
  try { applyAuth(await invoke<AuthStatus>('auth_set_persistence', { remember, sessionId })); }
  catch (error) { authError.value = String(error); }
  finally { persistenceBusy.value = false; rememberConfirm.value = false; }
}
async function refreshAuth() {
  cookieStatusLoading.value = true; authError.value = '';
  try { applyAuth(await invoke<AuthStatus>('auth_refresh')); }
  catch (error) { authError.value = String(error); }
  finally { cookieStatusLoading.value = false; }
}
const cookieStatus = ref<CookieStatus | null>(null);
const cookieStatusLoading = ref(false);
const directoryError = ref("");
const browserOptions = [
  { label: "Bilio 独立登录", value: "bilio" },
  { label: "不使用登录账号", value: "" }, { label: "Chrome", value: "chrome" },
  { label: "Edge", value: "edge" }, { label: "Brave", value: "brave" },
  { label: "Safari", value: "safari" }, { label: "Firefox", value: "firefox" },
  { label: "Chromium", value: "chromium" },
];
const cookieBadge = computed(() => {
  if (cookiesBrowser.value === 'bilio') {
    if (authStatus.value?.login_open) return '正在登录 B站';
    if (['initializing', 'checking'].includes(authStatus.value?.state || '')) return '正在验证账号';
    if (authStatus.value?.state === 'offline') return '账号待验证';
    if (authStatus.value?.state === 'expired') return '登录已失效';
    if (authStatus.value?.logged_in) return `B站 · ${authStatus.value.is_vip ? '大会员' : '已登录'}`;
    return '登录 B站';
  }
  const browser = browserOptions.find(o => o.value === cookiesBrowser.value)?.label || "";
  if (!cookiesBrowser.value) return "未连接账号";
  if (cookieStatusLoading.value) return `${browser} · 检测中`;
  if (cookieStatus.value?.logged_in) return `${browser} · ${cookieStatus.value.is_vip ? '大会员' : '已登录'}`;
  if (cookieStatus.value?.ok === false) return "账号连接失败";
  if (cookieStatus.value?.ok) return `${browser} · 未登录`;
  return `${browser} · 待检测`;
});
let probeVersion = 0;
async function probeCookies() {
  const version = ++probeVersion, browser = cookiesBrowser.value;
  if (!browser) { cookieStatus.value = null; cookieStatusLoading.value = false; return; }
  cookieStatusLoading.value = true;
  try {
    if (browser === 'bilio') { applyAuth(await invoke<AuthStatus>('auth_status')); return; }
    const status = await checkCookies(browser);
    if (version === probeVersion && browser === cookiesBrowser.value) cookieStatus.value = status;
  } catch (error) {
    if (version === probeVersion) cookieStatus.value = { ok: false, logged_in: false, username: null,
      is_vip: false, vip_label: null, error: String(error) };
  } finally { if (version === probeVersion) cookieStatusLoading.value = false; }
}
watch(cookiesBrowser, value => {
  ++probeVersion; cookieStatus.value = null; cookieStatusLoading.value = false;
  if (value && engineReady.value) void probeCookies();
});
watch(engineReady, async (ready, previous) => {
  if (!ready || previous) return;
  try { applyAuth(await invoke<AuthStatus>('auth_initialize')); }
  catch (error) { authError.value = String(error); }
  if (cookiesBrowser.value && cookiesBrowser.value !== 'bilio') void probeCookies();
  await saveConcurrency(concurrency.value);
});
onMounted(async () => {
  unlistenQuit = await listen('app://quit-requested', () => {
    if (tasks.value.some(isActive)) exitConfirm.value=true;
    else void invoke('confirm_exit');
  });
  unlistenAuth = await listen<AuthStatus>('auth://changed', event => applyAuth(event.payload));
  try { applyAuth(await invoke<AuthStatus>('auth_status')); } catch { authError.value = '无法获取登录状态，请重新打开应用'; }
  await initializeTasks();
  if (engineReady.value) {
    try { applyAuth(await invoke<AuthStatus>('auth_initialize')); }
    catch (error) { authError.value = String(error); }
  }
});
onUnmounted(() => { unlistenAuth?.();unlistenQuit?.(); });
function saveCookiesBrowser(value: string) {
  cookiesBrowser.value = value || null;
  if (value) localStorage.setItem("bilio-cookies-browser", value);
  else localStorage.removeItem("bilio-cookies-browser");
}
function saveDirectory(value: string) {
  downloadDir.value = value; localStorage.setItem("bilio-download-dir", value);
}
async function chooseDirectory() {
  try {
    const value = await invoke<string | null>("choose_download_directory");
    if (value) saveDirectory(value);
    directoryError.value = "";
  } catch { directoryError.value = "无法打开文件夹选择窗口，请重试"; }
}
</script>

<template>
  <n-config-provider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="theme">
    <n-message-provider><n-dialog-provider>
      <div class="app-shell">
        <header class="app-header">
          <div class="brand"><img :src="appIcon" width="30" height="30" alt="" /><h1>Bilio</h1><span>视频下载</span></div>
          <div class="header-actions">
            <n-button quaternary size="small" class="account-button" :loading="loginBusy" @click="cookiesBrowser === 'bilio' && !authStatus?.logged_in && (!authStatus?.state || authStatus.state === 'signed_out' || authStatus.login_open) ? login() : settingsOpen = true"><template #icon><CircleUserRound :size="16" /></template>{{ cookieBadge }}</n-button>
            <n-tooltip><template #trigger><n-button quaternary circle aria-label="设置" @click="settingsOpen = true"><Settings2 :size="18" /></n-button></template>设置</n-tooltip>
          </div>
        </header>
        <div v-if="engineError" class="engine-banner" role="alert"><span>{{ engineError }}</span><n-button size="small" @click="restartEngine().catch(e => engineError = String(e))"><template #icon><RefreshCw :size="14" /></template>重启引擎</n-button></div>
        <div v-if="historyWarning" class="engine-banner" role="alert"><span>{{ historyWarning }}</span><n-button size="small" @click="showDiagnostics">查看诊断</n-button></div>
        <MainArea :concurrency="concurrency" :download-dir="downloadDir" :cookies-browser="cookiesBrowser || undefined" :cookie-status="cookieStatus" :cookie-status-loading="cookieStatusLoading" :auth-revision="cookiesBrowser === 'bilio' ? authStatus?.session_id || '' : cookiesBrowser || ''" @settings="settingsOpen = true" />
      </div>
      <n-drawer v-model:show="settingsOpen" :width="'min(420px, 100vw)'" placement="right">
        <n-drawer-content title="设置" closable>
          <section class="settings-section"><h3>文件保存</h3><label for="download-directory">默认下载目录</label>
            <n-input id="download-directory" :value="downloadDir" placeholder="~/Downloads/Bilio" @update:value="saveDirectory" />
            <n-button class="folder-button" @click="chooseDirectory"><template #icon><FolderOpen :size="16" /></template>选择文件夹</n-button>
            <p v-if="directoryError" class="error-text">{{ directoryError }}</p>
          </section>
          <section class="settings-section"><h3>下载队列</h3><label>同时下载</label><n-select :value="concurrency" :options="[1,2,3].map(value=>({label:`${value} 个任务`,value}))" :disabled="!engineReady" aria-label="同时下载数量" @update:value="saveConcurrency" /></section>
          <section class="settings-section"><h3>B站账号</h3>
            <div v-if="cookiesBrowser === 'bilio'" class="independent-login"><span>{{ authStatus?.logged_in ? `${authStatus.username || '已登录'} · ${authStatus.is_vip ? '大会员' : '普通账号'}` : cookieBadge }}</span><n-button v-if="!authStatus?.logged_in" type="primary" :loading="loginBusy" :disabled="!engineReady || ['initializing','checking'].includes(authStatus?.state || '')" @click="login">{{ authStatus?.login_open ? '返回登录窗口' : '登录 B站' }}</n-button><n-popconfirm v-if="authStatus?.session_id || authStatus?.persistence === 'keychain' || authStatus?.state === 'offline'" @positive-click="logout" positive-text="退出登录" negative-text="返回"><template #trigger><n-button>退出本机登录</n-button></template>退出将停止使用此登录的未完成任务，并清除本机保存的登录。</n-popconfirm></div>
            <template v-if="cookiesBrowser === 'bilio'">
              <p class="muted">使用 B站官方网页登录，不读取其他浏览器的数据。默认仅本次运行有效，不影响手机端登录。</p>
              <n-checkbox :checked="authStatus?.persistence === 'keychain'" :disabled="!authStatus?.logged_in || persistenceBusy" :aria-disabled="!authStatus?.logged_in || persistenceBusy" @update:checked="changeRemember">在此 Mac 上保持登录</n-checkbox>
              <p v-if="authStatus?.persistence === 'keychain'" class="muted">登录已保存在系统钥匙串，下次启动时重新验证账号。</p>
              <n-button quaternary size="small" :disabled="!engineReady || authStatus?.state === 'signed_out'" :loading="cookieStatusLoading" @click="refreshAuth"><template #icon><RefreshCw :size="14" /></template>重新检测账号</n-button>
            </template>
            <p v-if="authError || authStatus?.error" class="error-text" role="alert">{{ authError || authStatus?.error }}</p>
            <n-collapse class="auth-advanced"><n-collapse-item title="其他登录来源" name="browser"><n-select :value="cookiesBrowser || ''" :options="browserOptions" @update:value="saveCookiesBrowser" /><div v-if="cookiesBrowser !== 'bilio'" class="account-state"><span :class="{ 'error-text': cookieStatus?.ok === false }">{{ cookieBadge }}</span><n-button v-if="cookiesBrowser" quaternary circle :loading="cookieStatusLoading" aria-label="重新检测账号" @click="probeCookies"><RefreshCw :size="15" /></n-button></div></n-collapse-item></n-collapse>
            <p v-if="cookieStatus?.error" class="error-text" role="alert">{{ cookieStatus.error }}</p>
            <p class="muted">会员画质取决于已登录账号的访问权限。</p>
          </section>
          <section class="settings-section privacy-note"><ShieldCheck :size="18" /><div><h3>本机处理</h3><p>浏览器登录数据仅用于向 B站验证账号权限，不上传至 Bilio 服务。不支持破解、DRM 解密或绕过付费验证。</p></div></section>
          <section class="settings-section"><h3>诊断</h3><n-button @click="showDiagnostics">预览脱敏诊断</n-button></section>
          <section class="settings-section"><h3>版本与更新</h3><n-button :loading="updateBusy" @click="checkUpdate">检查公开版本</n-button><p v-if="updateMessage" role="status" class="muted">{{ updateMessage }}</p><n-button v-if="updateUrl" @click="openUrl(updateUrl)">查看版本说明</n-button><p class="muted">本机候选版未公证。检查更新仅访问公开仓库，不下载或自动替换应用。</p></section>
          <div class="about-line"><span>Bilio 0.4.0 · 本机候选版</span><span :class="engineReady ? 'engine-ok' : ''">{{ engineReady ? '引擎就绪' : '引擎连接中' }}</span></div>
        </n-drawer-content>
      </n-drawer>
      <n-modal v-model:show="rememberConfirm" preset="dialog" title="在此 Mac 上保持登录？" positive-text="保存到系统钥匙串" negative-text="仅本次使用" :loading="persistenceBusy" :mask-closable="!persistenceBusy" :close-on-esc="!persistenceBusy" :closable="!persistenceBusy" @positive-click="setRemember(true, rememberRevision)">
        登录凭据将写入 macOS 系统钥匙串，供 Bilio 下次启动时恢复并验证账号，不上传至 Bilio 服务。退出本机登录会禁用自动恢复并删除已保存项。仅在你信任的 Mac 上开启。
      </n-modal>
      <n-modal v-model:show="exitConfirm" preset="dialog" title="退出 Bilio？" positive-text="停止并退出" negative-text="继续下载" @positive-click="invoke('confirm_exit')">正在进行的下载会停止，临时文件和任务记录保留，下次启动后可手动继续。已完成的视频不会删除。</n-modal>
      <n-modal v-model:show="diagnosticOpen" preset="card" title="脱敏诊断预览" style="width:min(580px,94vw);max-height:90vh;overflow:auto"><p class="muted">仅包含版本、系统架构、任务状态数量和错误类别。不含账号、凭据、视频名称、链接或个人路径，不会自动上传。</p><n-input :value="diagnosticText" type="textarea" readonly :autosize="{minRows:6,maxRows:15}" aria-label="脱敏诊断内容" /><div class="diagnostic-actions"><n-button @click="copyDiagnostics"><template #icon><Copy :size="15" /></template>复制诊断</n-button><n-button @click="exportDiagnostics"><template #icon><Download :size="15" /></template>导出诊断</n-button></div><p v-if="diagnosticError" role="status">{{ diagnosticError }}</p></n-modal>
    </n-dialog-provider></n-message-provider>
  </n-config-provider>
</template>

<style>
:root { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; color: #252a2d; background: #fff; font-synthesis: none; text-rendering: optimizeLegibility; -webkit-font-smoothing: antialiased; letter-spacing: 0; }
* { box-sizing: border-box; }
html, body, #app, #app > .n-config-provider { height: 100%; margin: 0; }
button, input { font: inherit; }
button:focus-visible, a:focus-visible { outline: 2px solid #167c69; outline-offset: 3px; }
.app-shell { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.app-header { height: 62px; min-height: 62px; padding: 0 24px; border-bottom: 1px solid #e7e9e9; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.brand, .header-actions { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand h1 { font-size: 18px; font-weight: 650; margin: 0; letter-spacing: 0; }
.brand > span { color: #71797e; font-size: 12px; padding-left: 12px; border-left: 1px solid #e1e5e4; }
.header-actions { gap: 8px; }
.engine-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; background: #fff0ed; color: #a83c32; padding: 8px 24px; font-size: 12px; }
.muted { color: #7a8284; font-size: 12px; line-height: 1.7; }
.error-text { color: #b7413d; font-size: 12px; overflow-wrap: anywhere; }
.settings-section { padding: 4px 0 26px; margin-bottom: 24px; border-bottom: 1px solid #e9eceb; }
.settings-section h3 { margin: 0 0 18px; font-size: 14px; font-weight: 600; }
.settings-section label { display: block; margin-bottom: 8px; color: #626c6f; font-size: 12px; }
.folder-button { margin-top: 12px; }
.account-state, .about-line { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; color: #687375; font-size: 12px; }
.privacy-note { display: flex; align-items: flex-start; gap: 10px; color: #6b7778; }
.privacy-note > svg { flex-shrink: 0; margin-top: 2px; }
.privacy-note h3 { margin-bottom: 8px; color: #333e40; }
.privacy-note p { font-size: 12px; line-height: 1.8; margin: 0; }
.engine-ok { color: #167c69; }
.independent-login { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; color: #687375; font-size: 12px; }
.auth-advanced { margin-top: 24px; }
.diagnostic-actions { display:flex; gap:8px; margin-top:16px; }
@media (max-width: 680px) { .app-header { padding: 0 16px; } .brand > span { display: none; } .account-button { max-width: 210px; } }
</style>
