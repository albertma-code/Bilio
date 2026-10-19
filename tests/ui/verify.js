async (page) => {
  const errors = [], passed = [];
  page.on('pageerror', error => errors.push(error.message));
  const check = (condition, name) => { if (!condition) throw new Error(name); passed.push(name); };
  await page.addInitScript(() => {
    localStorage.clear();
    const callbacks = new Map(), listeners = new Map();
    let next = 0;
    const state = { tasks: [], submitted: [], resumed:[], persistenceRequests:[], denyPersistence:false, finishDuringClear: false, parseMode: 'single', deferParse: false, deferred: null, removed: [], opened: [], auth: { ok:true, logged_in:false, is_vip:false, username:null, vip_label:null, error:null, session_id:null, login_open:false, persistence:'session',state:'signed_out' } };
    const emit = message => { for (const entry of listeners.values()) if (entry.event === 'sidecar://message') callbacks.get(entry.handler)?.({ payload: JSON.stringify(message) }); };
    const emitAuth = status => { state.auth = status; for (const entry of listeners.values()) if (entry.event === 'auth://changed') callbacks.get(entry.handler)?.({ payload: status }); };
    const formats = [
      { format_id: '4k', format_note: '4K 超高清', width: 3840, height: 1632, quality: 120, vcodec: 'avc1', acodec: 'none', ext: 'mp4', filesize: 1400000000 },
      { format_id: '1080', format_note: '1080P 高清', width: 1920, height: 816, quality: 80, vcodec: 'avc1', acodec: 'none', ext: 'mp4', filesize: 400000000 },
      { format_id: 'hevc', format_note: '1080P 高清', width: 1920, height: 816, quality: 80, vcodec: 'hev1', acodec: 'none', ext: 'mp4', filesize: 220000000 },
      { format_id: 'audio', format_note: 'audio only', quality: 0, vcodec: 'none', acodec: 'aac', ext: 'm4a', filesize: 70000000 },
    ];
    const data = url => ({ title: '演示视频', display_title: '示例番剧 第一季 · 第 1 集', kind: 'bangumi', uploader: null, duration: 3515,
      thumbnail: null, webpage_url: url, is_playlist: state.parseMode === 'season', formats: state.parseMode === 'season' ? [] : formats,
      entries: state.parseMode !== 'season' ? [] : Array.from({ length: 8 }, (_, i) => ({ id: String(i + 1), title: `第 ${i + 1} 集`,
        duration: 3500 + i, url: `https://www.bilibili.com/bangumi/play/ep${i + 1}`, detail_status: i === 7 ? 'error' : 'ok',
        best_format_note: i === 7 ? null : '4K 超高清', best_width: 3840, best_height: 1632 })) });
    window.__bilioTest = { state, emit, emitAuth, emitUi:(event,payload)=>{for(const entry of listeners.values())if(entry.event===event)callbacks.get(entry.handler)?.({payload});}, finishParse: () => { const a = state.deferred; emit({ id: a.jobId, type: 'result', data: data(a.url) }); } };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: (_event, id) => listeners.delete(id) };
    window.__TAURI_INTERNALS__ = {
      transformCallback: callback => { const id = ++next; callbacks.set(id, callback); return id; },
      unregisterCallback: id => callbacks.delete(id),
      invoke: async (command, args) => {
        if (command === 'plugin:event|listen') { listeners.set(args.handler, {handler:args.handler,event:args.event}); return args.handler; }
        if (command === 'plugin:event|unlisten') { listeners.delete(args.eventId); return; }
        if (command.startsWith('plugin:opener|')) { state.opened.push({ command, args }); return; }
        if (command === 'alloc_job_id') return ++next;
        if (command === 'ping_sidecar') return { type: 'pong', ts: 0 };
        if(command==='diagnostic_report')return {app_version:'0.4.0',os:'macos',arch:'aarch64',engine:{states:{completed:1}}};
        if(command==='export_diagnostics'){state.exports=(state.exports||0)+1;return true;}
        if(command==='check_update')return {status:'no_release'};
        if(command==='confirm_exit'){state.exits=(state.exits||0)+1;return;}
        if (command === 'check_cookies') return { ok: true, logged_in: true, is_vip: true, username: 'Test' };
        if (['auth_status', 'auth_initialize', 'auth_refresh'].includes(command)) return state.auth;
        if (command === 'auth_set_persistence') {
          state.persistenceRequests.push(args);
          if (state.denyPersistence) throw new Error('系统钥匙串未能保存登录；仍可使用本次会话，未写入明文文件');
          emitAuth({...state.auth,persistence:args.remember ? 'keychain' : 'session'}); return state.auth;
        }
        if (command === 'auth_login') { emitAuth({...state.auth,login_open:true}); return state.auth; }
        if (command === 'auth_logout') { emitAuth({...state.auth,logged_in:false,is_vip:false,session_id:null,login_open:false,state:'signed_out',persistence:'session'}); return state.auth; }
        if (command === 'choose_download_directory') return state.nextDirectory || '/tmp/bilio-selected';
        if (command === 'restart_sidecar') { emit({ type: 'ready', id: null }); return; }
        if (command === 'start_parse') {
          if (state.deferParse) state.deferred = args;
          else emit({ id: args.jobId, type: 'result', data: data(args.url) });
          return;
        }
        if (command === 'task_command') {
          if(args.command==='set_concurrency')return {concurrency:args.args.concurrency};
          if(args.command==='pause'||args.command==='resume') {
            const task=state.tasks.find(t=>t.jobId===args.args.job_id);
            if(args.command==='resume')state.resumed.push(JSON.parse(JSON.stringify(task.config)));
            task.status=args.command==='pause'?'paused':task.jobId===902?'queued':'completed';task.sequence++;
            emit({id:task.jobId,type:'task',data:task});return task;
          }
          if (args.command === 'tasks') return state.tasks;
          if (args.command === 'clear_finished' || args.command === 'remove_finished') {
            const removed = state.tasks.filter(t => ['completed','cancelled','failed','interrupted'].includes(t.status) &&
              (args.command === 'clear_finished' || t.jobId === args.args.job_id)).map(t => t.jobId);
            state.tasks = state.tasks.filter(t => !removed.includes(t.jobId)); state.removed.push(...removed);
            if (state.finishDuringClear) { const task = state.tasks[0]; task.status = 'completed'; task.sequence++; emit({ id: task.jobId, type: 'task', data: task }); state.finishDuringClear = false; }
            return { removed };
          }
          if (args.command === 'cancel' || args.command === 'cancel_batch') {
            state.tasks.filter(t => args.command === 'cancel' ? t.jobId === args.args.job_id : t.config.batchId === args.args.batch_id).forEach(t => {
              if (['queued','downloading','starting','processing'].includes(t.status)) { t.status = 'cancelled'; t.sequence++; emit({ id: t.jobId, type: 'task', data: t }); }
            }); return { requested: true };
          }
          if (args.command === 'download') {
            if (args.args.cookies_from_browser === 'bilio') args.args.auth_session_id = state.auth.session_id;
            state.submitted.push(args.args);
            const task = { jobId: args.args.job_id, config: args.args, sequence: 3, status: 'completed', progress: 100,
              downloadedBytes: 290000000, totalBytes: 290000000, filename: 'video.f30280.m4a', filepath: '/tmp/final-video.mp4' };
            state.tasks.push(task); emit({ id: task.jobId, type: 'task', data: task });
            return { ...task, sequence: 1, status: 'queued' };
          }
        }
      }
    };
  });
  await page.setViewportSize({ width: 1100, height: 740 });
  await page.goto('http://127.0.0.1:1421/');
  await page.getByText('引擎就绪', { exact: true }).waitFor();
  await page.getByText('还没有下载任务', { exact: true }).waitFor();
  check(await page.getByRole('navigation', { name: '下载视图' }).isVisible(), 'download library is first screen');
  await page.getByRole('button', { name: '登录 B站', exact: true }).click();
  await page.getByRole('button', { name: '正在登录 B站', exact: true }).waitFor(); passed.push('independent login is default entry');
  await page.evaluate(() => { const t = window.__bilioTest; t.emitAuth({...t.state.auth,logged_in:true,is_vip:true,username:'Test',session_id:'session-one',login_open:false,state:'verified'}); });
  await page.getByRole('button', { name: 'B站 · 大会员', exact: true }).waitFor(); passed.push('native login status updates header');
  await page.locator('.sidebar-add').click();
  const input = page.getByPlaceholder('粘贴 B站视频、番剧或合集链接');
  await input.fill('https://www.bilibili.com/bangumi/play/ep1000000000');
  await page.evaluate(() => { window.__bilioTest.state.deferParse = true; });
  await page.getByRole('button', { name: '解析', exact: true }).click();
  await page.getByText('正在获取视频信息', { exact: true }).waitFor();
  check(await page.getByText('未取得可下载内容', { exact: true }).count() === 0, 'loading never displays empty failure');
  await page.evaluate(() => { window.__bilioTest.finishParse(); window.__bilioTest.state.deferParse = false; });
  await page.getByText('示例番剧 第一季 · 第 1 集', { exact: true }).waitFor();
  check((await page.locator('.submit-bar').innerText()).includes('1080P 高清 · H.264'), 'default 1080p H264 instead of maximum');
  check((await page.locator('.size-summary').innerText()).includes('MiB'), 'estimated audio plus video size');
  await input.fill('https://www.bilibili.com/video/BVchanged');
  await page.getByText('链接或账号来源已改变，请重新解析后下载。').waitFor();
  check(await page.getByRole('button', { name: '加入下载列表', exact: true }).isDisabled(), 'stale source blocked');
  await input.fill('https://www.bilibili.com/bangumi/play/ep1000000000');
  await page.getByText('高级选项', { exact: true }).click();
  await page.locator('[aria-label="视频编码"]').click();
  await page.getByText('HEVC · 更小体积', { exact: true }).click();
  await page.getByRole('button', { name: '加入下载列表', exact: true }).click();
  await page.locator('.task-row.completed').waitFor();
  const submitted = await page.evaluate(() => window.__bilioTest.state.submitted);
  check(submitted.length === 1 && submitted[0].policy.format_id === 'hevc', 'codec exact policy snapshot');
  check(submitted[0].formatLabel === '1080P 高清 · HEVC', 'task label hides technical format ids');
  check(submitted[0].cookies_from_browser === 'bilio' && submitted[0].auth_session_id === 'session-one', 'independent task binds session without credentials');
  await page.locator('.task-title').click();
  await page.getByText('final-video.mp4', { exact: true }).waitFor();
  check(await page.getByText('video.f30280.m4a', { exact: true }).count() === 0, 'legacy completed filename uses final filepath');
  await page.keyboard.press('Escape');
  await page.evaluate(() => {
    const t = window.__bilioTest, base = t.state.tasks[0];
    const add = (id, status, title, batchId) => { const task = { ...base, jobId: id, status, sequence: 5, progress: 42,
      config: { ...base.config, title, batchId }, errorMessage: status === 'failed' ? '网络连接中断，请重试' : null };
      t.state.tasks.push(task); t.emit({ id, type: 'task', data: task }); };
    add(901, 'downloading', '正在下载的视频', 'batch-demo'); add(902, 'queued', '等待下载的视频', 'batch-demo');
    add(903, 'failed', '失败后重试的视频');
    for (let i = 0; i < 40; i++) add(1000+i, 'completed', `已完成的视频 ${i+1} 很长的视频名称用于检验窗口排版与换行`);
  });
  await page.getByRole('button', { name: '进行中', exact: false }).click();
  check(await page.locator('.task-row').count() === 2, 'active filter');
  await page.getByRole('button',{name:'折叠批次',exact:true}).click();
  check(await page.locator('.task-row').count()===0,'batch collapses without changing tasks');
  await page.getByRole('button',{name:'展开批次',exact:true}).click();
  await page.getByRole('checkbox',{name:'选择任务 等待下载的视频',exact:true}).click();
  await page.getByRole('button',{name:'暂停所选任务',exact:true}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await page.getByRole('button',{name:'已暂停',exact:false}).click();
  await page.locator('[data-task-id="902"].paused').waitFor();passed.push('bulk pause applies to selected task');
  await page.getByRole('checkbox',{name:'选择任务 等待下载的视频',exact:true}).click();
  await page.getByRole('button',{name:'继续所选任务',exact:true}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await page.locator('.task-row.paused').waitFor({state:'detached'});passed.push('bulk resume applies to selected task');
  await page.getByRole('button',{name:'进行中',exact:false}).click();
  await page.locator('[data-task-id="901"]').getByRole('button',{name:'暂停下载',exact:true}).click();
  await page.getByRole('button',{name:'已暂停',exact:false}).click();
  await page.locator('.task-row.paused').waitFor();passed.push('paused view and pause control');
  await page.getByRole('button',{name:'继续下载',exact:true}).click();
  await page.locator('.task-row.paused').waitFor({state:'detached'});passed.push('resume keeps original task');
  await page.getByRole('button', { name: '需要处理', exact: false }).click();
  check(await page.locator('.task-row').count() === 1, 'attention filter');
  await page.getByRole('button', { name: '按原配置重试', exact: true }).click();
  check((await page.evaluate(() => window.__bilioTest.state.resumed)).at(-1).title === '失败后重试的视频', 'retry preserves original config');
  await page.getByRole('button', { name: '全部任务', exact: false }).click();
  const search = page.getByPlaceholder('搜索任务名称'); await search.fill('失败后重试');
  check(await page.locator('.task-row').count() === 1, 'search across history');
  await search.fill('没有匹配的名字'); await page.getByText('没有匹配的任务', { exact: true }).waitFor();
  passed.push('search empty state'); await search.fill('');
  await page.locator('[data-task-id="903"]').getByRole('button', { name: '更多操作', exact: true }).click();
  await page.getByText('移除记录', { exact: true }).click();
  await page.getByText('仅移除列表记录，本地视频文件不会删除。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '移除记录', exact: true }).click();
  await page.locator('[data-task-id="903"]').waitFor({ state: 'detached' }); passed.push('single record removal requires confirmation');
  await page.getByRole('button', { name: '进行中', exact: false }).click();
  await page.getByRole('button', { name: '取消批次', exact: true }).click();
  await page.getByRole('button', { name: '取消批次', exact: true }).last().click();
  await page.getByText('当前没有进行中的下载', { exact: true }).waitFor(); passed.push('batch grouping and cancellation');
  await page.getByRole('button', { name: '全部任务', exact: false }).click();
  await page.evaluate(() => {
    const t = window.__bilioTest, task = { ...t.state.tasks[0], jobId: 9999, status: 'downloading', sequence: 1, config: { ...t.state.tasks[0].config, title: '清理期间完成的任务' } };
    t.state.tasks.push(task); t.emit({ id: task.jobId, type: 'task', data: task }); t.state.finishDuringClear = true;
    // Keep the active task first for the simulated clear/completion race.
    t.state.tasks = [task, ...t.state.tasks.filter(x => x.jobId !== task.jobId)];
  });
  for (const width of [1100, 800, 640]) {
    await page.setViewportSize({ width, height: 700 });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow ' + width);
    const toolbar = await page.locator('.library-tools').boundingBox(), footer = await page.locator('.library-footer').boundingBox();
    check(toolbar.y >= 0 && footer.y + footer.height <= 701, 'toolbar and footer remain visible ' + width);
    await page.screenshot({ path: '/tmp/bilio-v030-library-' + width + '.png', fullPage: true });
  }
  await page.getByRole('button', { name: '清空已结束记录', exact: true }).click();
  await page.getByRole('button', { name: '仅清空记录', exact: true }).click();
  await page.getByRole('button', { name: '清空已结束记录', exact: true }).waitFor();
  await page.getByText('清理期间完成的任务', { exact: true }).waitFor();
  check((await page.evaluate(() => window.__bilioTest.state.tasks)).length === 1, 'clear completion race retains new completion');
  await page.evaluate(() => { const t = window.__bilioTest, task = t.state.tasks[0]; t.emit({ id: task.jobId, type: 'task', data: { ...task, sequence: 1, status: 'downloading' } }); });
  check(await page.locator('.task-row.completed').count() === 1, 'out of order events rejected');
  await page.getByRole('button', { name: '新建下载', exact: true }).first().click();
  await page.evaluate(() => { const t = window.__bilioTest; t.emitAuth({...t.state.auth,session_id:'session-two'}); });
  await page.getByText('链接或账号来源已改变，请重新解析后下载。').waitFor();
  check(await page.getByRole('button', { name: '加入下载列表', exact: true }).isDisabled(), 'same source new session invalidates parse');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'B站 · 大会员', exact: true }).click();
  const remember = page.getByRole('checkbox', { name:'在此 Mac 上保持登录', exact:true });
  check(!(await remember.isChecked()), 'remember login is opt-in');
  await remember.click();
  await page.getByRole('button', { name:'仅本次使用',exact:true }).click();
  check((await page.evaluate(() => window.__bilioTest.state.persistenceRequests.length)) === 0, 'cancel confirmation never invokes credential save');
  await remember.click();
  await page.getByRole('button', { name:'保存到系统钥匙串',exact:true }).click();
  await page.getByText('登录已保存在系统钥匙串，下次启动时重新验证账号。', {exact:true}).waitFor();
  check(await remember.isChecked(), 'explicit confirmation enables persistence');
  const persistence = await page.evaluate(() => window.__bilioTest.state.persistenceRequests[0]);
  check(persistence.remember && persistence.sessionId === 'session-two', 'save confirmation bound to reviewed session');
  await remember.click();
  await page.evaluate(() => { window.__bilioTest.state.denyPersistence = true; });
  await remember.click(); await page.getByRole('button', { name:'保存到系统钥匙串',exact:true }).click();
  await page.getByText('Error: 系统钥匙串未能保存登录；仍可使用本次会话，未写入明文文件', {exact:true}).waitFor();
  check(!(await remember.isChecked()), 'keychain denial does not claim persistence');
  await page.evaluate(() => { window.__bilioTest.state.denyPersistence = false; const t=window.__bilioTest;t.emitAuth({...t.state.auth,logged_in:false,is_vip:false,state:'offline'}); });
  await page.getByRole('button', {name:'账号待验证',exact:true}).waitFor();
  check(await remember.isDisabled(), 'unverified account cannot be remembered');
  check(await page.getByRole('button', {name:'退出本机登录',exact:true}).isVisible(), 'offline account retains logout entry');
  await page.getByRole('button', {name:'重新检测账号',exact:true}).waitFor();
  await page.evaluate(() => { const t=window.__bilioTest;t.emitAuth({...t.state.auth,logged_in:true,is_vip:true,state:'verified'}); });
  await page.getByRole('button', { name: '退出本机登录', exact: true }).click();
  check(await page.getByRole('button', { name:'B站 · 大会员',exact:true }).isVisible(), 'logout waits for confirmation');
  await page.getByRole('button', { name:'退出登录',exact:true }).click();
  await page.getByRole('button', { name: '登录 B站', exact: true }).first().waitFor(); passed.push('logout clears independent account status');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('button', { name: '选择文件夹', exact: true }).click();
  check((await page.getByPlaceholder('~/Downloads/Bilio', { exact: true }).inputValue()) === '/tmp/bilio-selected', 'folder picker persistence');
  await page.getByText('其他登录来源', { exact: true }).click();
  await page.getByText('Bilio 独立登录', { exact: true }).click(); await page.getByText('Chrome', { exact: true }).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Chrome · 大会员', exact: true }).waitFor(); passed.push('account status compact header');
  await page.getByRole('button', { name: '新建下载', exact: true }).first().click();
  await page.getByText('链接或账号来源已改变，请重新解析后下载。').waitFor(); passed.push('account change invalidates parse');
  await page.evaluate(() => { window.__bilioTest.state.parseMode = 'season'; });
  await page.getByRole('button', { name: '解析', exact: true }).click();
  await page.getByText('已选 7 / 8', { exact: true }).waitFor(); passed.push('failed episodes never default selected');
  check((await page.locator('[aria-label="批量下载画质"]').innerText()).includes('1080P'), 'batch quality conservative default');
  await page.getByRole('textbox',{name:'搜索分集',exact:true}).fill('第 2 集');
  check(await page.getByRole('cell',{name:'第 2 集',exact:true}).isVisible(), 'episode search retains matching entry');
  await page.getByRole('textbox',{name:'搜索分集',exact:true}).fill('');
  await page.getByRole('textbox',{name:'分集范围',exact:true}).fill('1-3,5');
  await page.getByRole('button',{name:'应用分集范围',exact:true}).click();
  await page.getByText('已选 4 / 8',{exact:true}).waitFor();passed.push('non-contiguous episode range selection');
  await page.evaluate(()=>window.__bilioTest.state.parseMode='single');
  await page.getByRole('button',{name:'重试失败分集',exact:true}).click();
  await page.getByRole('button',{name:'重试失败分集',exact:true}).waitFor({state:'detached'});passed.push('repair only failed episode');
  await page.getByRole('textbox',{name:'分集范围',exact:true}).fill('1-7');
  await page.getByRole('button',{name:'应用分集范围',exact:true}).click();
  await page.getByText('已选 7 / 8',{exact:true}).waitFor();
  await page.evaluate(()=>window.__bilioTest.state.nextDirectory='/tmp/bilio-just-this-task');
  await page.locator('.destination').click();
  check(await page.locator('.destination').innerText()==='/tmp/bilio-just-this-task','per-task directory override');
  check(await page.evaluate(()=>localStorage.getItem('bilio-download-dir'))==='/tmp/bilio-selected','per-task directory leaves default unchanged');
  for (const width of [1100, 640]) {
    await page.setViewportSize({ width, height: 700 });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'parse panel no overflow ' + width);
    const submit = await page.locator('.submit-bar').boundingBox();
    check(submit.y >= 0 && submit.y + submit.height <= 701, 'parse submit always visible ' + width);
    await page.screenshot({ path: '/tmp/bilio-v030-parser-' + width + '.png', fullPage: true });
  }
  await page.setViewportSize({ width: 640, height: 480 });
  const smallSubmit = await page.locator('.submit-bar').boundingBox();
  check(smallSubmit.y >= 0 && smallSubmit.y + smallSubmit.height <= 481, 'submit visible at minimum 640x480');
  await page.screenshot({ path: '/tmp/bilio-v030-parser-minimum.png', fullPage: true });
  await page.locator('.result').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.getByRole('button', { name: '加入下载列表 (7)', exact: true }).click();
  await page.getByRole('button', { name: '加入下载列表', exact: true }).click();
  await page.getByText('7 个分集已加入下载列表', { exact: true }).waitFor();
  check((await page.evaluate(() => window.__bilioTest.state.submitted)).slice(-7).every(t => t.policy.mode === 'max_quality' && t.policy.quality === 80 && t.batchId), 'batch submission bound and grouped');
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.__bilioTest.emit({ id: null, type: 'engine_exit' }));
  await page.getByRole('button', { name: '重启引擎', exact: true }).click();
  await page.setViewportSize({ width: 1100, height: 740 });
  await page.getByText('引擎就绪', { exact: true }).waitFor(); passed.push('engine restart');
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await page.getByRole('button',{name:'检查公开版本',exact:true}).click();
  await page.getByText('尚无公开正式版本',{exact:true}).waitFor();passed.push('update check distinguishes no public release');
  await page.getByRole('button',{name:'预览脱敏诊断',exact:true}).click();
  await page.getByText('脱敏诊断预览',{exact:true}).waitFor();
  check((await page.locator('.n-modal textarea').inputValue()).includes('0.4.0'),'diagnostics preview is inspectable');
  check((await page.evaluate(()=>window.__bilioTest.state.exports||0))===0,'diagnostics not exported before user action');
  await page.getByRole('button',{name:'导出诊断',exact:true}).click();
  await page.getByText('已导出脱敏诊断',{exact:true}).waitFor();passed.push('diagnostic export is explicit');
  await page.keyboard.press('Escape');await page.keyboard.press('Escape');
  await page.evaluate(()=>{const t=window.__bilioTest,base=t.state.tasks[0],task={...base,jobId:200001,sequence:1,status:'downloading'};t.state.tasks.push(task);t.emit({id:task.jobId,type:'task',data:task});t.emitUi('app://quit-requested',null);});
  await page.getByRole('button',{name:'继续下载',exact:true}).click();
  check((await page.evaluate(()=>window.__bilioTest.state.exits||0))===0,'quit can be cancelled while downloads active');
  await page.evaluate(()=>window.__bilioTest.emitUi('app://quit-requested',null));
  await page.getByRole('button',{name:'停止并退出',exact:true}).click();
  check((await page.evaluate(()=>window.__bilioTest.state.exits))===1,'quit requires explicit confirmation for active work');
  check(errors.length === 0, 'no browser runtime errors');
  return { passed, errors };
}
