// Development-only harness: real UI components, synthetic tasks, no native calls.
if (!import.meta.env.DEV) throw new Error('Preview fixtures must not run in a release build');
const callbacks = new Map<number, (event: unknown) => void>();
const listeners = new Map<number, { handler: number; event: string }>();
let next = 10000;
const auth = { ok: true, logged_in: true, is_vip: true, username: '预览账号', vip_label: '大会员', error: null,
  session_id: 'preview-session', login_open: false, persistence: 'session' };
const config = { url: 'https://www.bilibili.com/video/BVpreview', title: '', policy: {mode:'exact',format_id:'preview'},
  output_dir: '~/Downloads/Bilio', formatLabel: '1080P 高清 · H.264', cookies_from_browser: 'bilio', auth_session_id:'preview-session' };
const tasks: any[] = [
  { jobId: 106, status:'downloading', config:{...config,title:'城市漫步 · 雨后的街道'},progress:46,downloadedBytes:171966464,totalBytes:373293056,speed:7549747,eta:27 },
  { jobId: 105, status:'queued', config:{...config,title:'旅行影像 · 山间的清晨'},progress:null },
  { jobId: 104, status:'failed', config:{...config,title:'建筑观察 · 城市里的图书馆'},errorMessage:'网络连接中断，请检查连接后重试。' },
  { jobId: 103, status:'completed', config:{...config,title:'自然记录 · 海岸线'},totalBytes:287309824,filepath:'/preview/海岸线.mp4' },
];
for (const task of tasks) Object.assign(task, { sequence:1,filename:'',downloadedBytes:task.downloadedBytes || 0 });
const emit = (message: unknown) => {
  for (const entry of listeners.values()) if (entry.event === 'sidecar://message') callbacks.get(entry.handler)?.({payload:JSON.stringify(message)});
};
function update(task: any, status: string) { task.status=status;task.sequence++;emit({id:task.jobId,type:'task',data:{...task}}); }
const mockWindow = window as any;
mockWindow.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener: (_event: string, id: number) => listeners.delete(id)};
mockWindow.__TAURI_INTERNALS__ = {
  transformCallback: (callback: (event: unknown) => void) => { const id=++next; callbacks.set(id,callback);return id; },
  unregisterCallback: (id: number) => callbacks.delete(id),
  invoke: async (command: string, args: any = {}) => {
    if (command === 'plugin:event|listen') { listeners.set(args.handler,{handler:args.handler,event:args.event});return args.handler; }
    if (command === 'plugin:event|unlisten') { listeners.delete(args.eventId);return; }
    if (command.startsWith('plugin:opener|')) return;
    if (command === 'alloc_job_id') return ++next;
    if (command === 'ping_sidecar') return {type:'pong',ts:0};
    if (['auth_status', 'auth_initialize', 'auth_refresh', 'check_cookies'].includes(command)) return {...auth, state:'verified'};
    if (command === 'auth_set_persistence') return {...auth, state:'verified', persistence: args.remember ? 'keychain' : 'session'};
    if (command === 'choose_download_directory') return null;
    if (command === 'diagnostic_report') return {app_version:'preview',os:'macos',arch:'aarch64',engine:{states:{downloading:1,completed:1,failed:1}}};
    if (command === 'export_diagnostics') return false;
    if (command === 'confirm_exit') return;
    if (command === 'auth_logout') return {...auth,logged_in:false,is_vip:false,session_id:null};
    if (command === 'auth_login') return {...auth};
    if (command === 'task_command') {
      if(args.command==='storage_status')return {unreadable_records:0};
      if(args.command==='set_concurrency')return {concurrency:args.args.concurrency};
      if(args.command==='pause'||args.command==='resume') {const task=tasks.find(t=>t.jobId===args.args.job_id);if(task)update(task,args.command==='pause'?'paused':'queued');return task;}
      if(args.command==='temp_info'||args.command==='clear_temp')return {file_count:0,bytes:0};
      if (args.command === 'tasks') return tasks;
      if (args.command === 'cancel') { const task=tasks.find(t=>t.jobId===args.args.job_id);if(task) update(task,'cancelled');return {requested:true}; }
      if (args.command === 'clear_finished' || args.command === 'remove_finished') {
        const removed=tasks.filter(t=>['completed','failed','cancelled'].includes(t.status) && (args.command==='clear_finished'||t.jobId===args.args.job_id)).map(t=>t.jobId);
        for(let i=tasks.length-1;i>=0;i--) if(removed.includes(tasks[i].jobId)) tasks.splice(i,1);
        return {removed};
      }
      if(args.command==='download') {const task={...tasks[0],jobId:args.args.job_id,config:args.args,status:'queued',sequence:1};tasks.push(task);return task;}
    }
    if (command === 'start_parse') {
      emit({id:args.jobId,type:'result',data:{title:'城市漫步 · 雨后的街道',display_title:'城市漫步 · 雨后的街道',kind:'single',uploader:'影像记录',duration:738,thumbnail:null,webpage_url:args.url,is_playlist:false,entries:[],formats:[
        {format_id:'1080',format_note:'1080P 高清',width:1920,height:1080,quality:80,vcodec:'avc1',acodec:'none',ext:'mp4',filesize:373293056},
        {format_id:'hevc',format_note:'1080P 高清',width:1920,height:1080,quality:80,vcodec:'hev1',acodec:'none',ext:'mp4',filesize:219152384},
        {format_id:'audio',format_note:'音频',vcodec:'none',acodec:'aac',ext:'m4a',filesize:20971520},
      ]}});return;
    }
    throw new Error(`Preview command not implemented: ${command}`);
  },
};
localStorage.setItem('bilio-auth-mode-v1','1');
localStorage.setItem('bilio-cookies-browser','bilio');
localStorage.setItem('bilio-download-dir','~/Downloads/Bilio');
await import('../../src/main');
