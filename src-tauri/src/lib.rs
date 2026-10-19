//! JSONL bridge. Task snapshots are owned by the sidecar, not the webview.
mod auth;
mod credential_store;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_dialog::DialogExt;
use tokio::sync::oneshot;

struct SidecarState {
    child: Mutex<Option<CommandChild>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Value>>>,
    next_id: AtomicU64,
}
struct ExitPermission(AtomicBool);

#[cfg(target_os = "macos")]
fn install_guarded_menu(app: &AppHandle) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
    let menu = Menu::default(app)?;
    // Cocoa's predefined Quit invokes terminate: directly. Use a normal menu
    // command so Cmd+Q follows the same confirmation path as closing a window.
    let quit = MenuItem::with_id(app, "bilio.request-quit", "退出 Bilio", true, Some("CmdOrCtrl+Q"))?;
    let app_menu = Submenu::with_items(app, "Bilio", true, &[
        &PredefinedMenuItem::about(app, None, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::services(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &PredefinedMenuItem::hide(app, None)?,
        &PredefinedMenuItem::hide_others(app, None)?,
        &PredefinedMenuItem::show_all(app, None)?,
        &PredefinedMenuItem::separator(app)?,
        &quit,
    ])?;
    menu.remove_at(0)?;
    menu.insert(&app_menu, 0)?;
    app.set_menu(menu)?;
    Ok(())
}

#[tauri::command]
async fn confirm_exit(app: AppHandle) {
    app.state::<ExitPermission>().0.store(true, Ordering::SeqCst);
    app.exit(0);
}

#[tauri::command]
async fn diagnostic_report(app: AppHandle) -> Result<Value, String> {
    let engine = request(&app.state::<SidecarState>(), "diagnostics", serde_json::json!({})).await
        .unwrap_or(serde_json::json!({"status":"unavailable"}));
    Ok(serde_json::json!({"app_version":app.package_info().version.to_string(),"os":std::env::consts::OS,
        "arch":std::env::consts::ARCH,"distribution":"local-unsigned-candidate","engine":engine}))
}

#[tauri::command]
async fn check_update(app: AppHandle) -> Result<Value, String> {
    request(&app.state::<SidecarState>(), "check_update", serde_json::json!({})).await
}

#[tauri::command]
async fn export_diagnostics(app: AppHandle) -> Result<bool, String> {
    // Generate natively: never accept arbitrary frontend history or raw errors.
    let report = diagnostic_report(app.clone()).await?;
    tauri::async_runtime::spawn_blocking(move || {
        let file = app.dialog().file().set_title("导出脱敏诊断").add_filter("JSON", &["json"])
            .set_file_name("Bilio-diagnostics.json").blocking_save_file();
        let Some(file) = file else { return Ok(false); };
        let path = file.into_path().map_err(|_| "无效的导出位置")?;
        std::fs::write(path, serde_json::to_vec_pretty(&report).map_err(|_| "诊断编码失败")?)
            .map_err(|_| "诊断保存失败，请检查目录权限和空间")?;
        Ok(true)
    }).await.map_err(|_| "无法打开导出窗口")?
}

impl SidecarState {
    fn new() -> Self {
        let seed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default().as_millis() as u64 * 1000;
        Self { child: Mutex::new(None), pending: Mutex::new(HashMap::new()), next_id: AtomicU64::new(seed) }
    }
    fn alloc_id(&self) -> u64 { self.next_id.fetch_add(1, Ordering::Relaxed) }
}

fn spawn_sidecar(app: &AppHandle, state: &SidecarState) -> Result<(), String> {
    let mut guard = state.child.lock().unwrap();
    if guard.is_some() { return Ok(()); }
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|e| e.to_string())?;
    let (mut rx, child) = app.shell().sidecar("bilio-sidecar")
        .map_err(|e| e.to_string())?.env("BILIO_TASK_DB", data_dir.join("tasks.sqlite3"))
        .spawn().map_err(|e| e.to_string())?;
    *guard = Some(child);
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => handle_line(&app, String::from_utf8_lossy(&bytes).trim()),
                CommandEvent::Stderr(bytes) => eprintln!("[sidecar] {}", String::from_utf8_lossy(&bytes)),
                CommandEvent::Terminated(_) => break,
                CommandEvent::Error(error) => eprintln!("[sidecar] {error}"),
                _ => {}
            }
        }
        let state: State<SidecarState> = app.state();
        state.child.lock().unwrap().take();
        state.pending.lock().unwrap().clear();
        let _ = app.emit_to("main", "sidecar://message", r#"{"id":null,"type":"engine_exit"}"#);
    });
    Ok(())
}

fn handle_line(app: &AppHandle, line: &str) {
    let Ok(mut msg) = serde_json::from_str::<Value>(line) else { return; };
    if matches!(msg.get("type").and_then(Value::as_str), Some("result" | "error" | "pong")) {
        if let Some(id) = msg.get("id").and_then(Value::as_u64) {
            let state: State<SidecarState> = app.state();
            let tx = state.pending.lock().unwrap().remove(&id);
            if let Some(tx) = tx { let _ = tx.send(msg); return; }
        }
    }
    // Private RPC responses that arrive after their timeout must not expose a
    // raw site account ID through the generic frontend event channel.
    if let Some(data) = msg.get_mut("data").and_then(Value::as_object_mut) { data.remove("account_id"); }
    let _ = app.emit_to("main", "sidecar://message", msg.to_string());
}

fn send(state: &SidecarState, id: u64, cmd: &str, args: Value) -> Result<(), String> {
    let line = serde_json::json!({"id": id, "cmd": cmd, "args": args}).to_string();
    let mut guard = state.child.lock().unwrap();
    guard.as_mut().ok_or("下载引擎未运行")?
        .write(format!("{line}\n").as_bytes()).map_err(|e| e.to_string())
}

async fn request(state: &SidecarState, cmd: &str, args: Value) -> Result<Value, String> {
    let id = state.alloc_id();
    let (tx, rx) = oneshot::channel();
    state.pending.lock().unwrap().insert(id, tx);
    if let Err(error) = send(state, id, cmd, args) {
        state.pending.lock().unwrap().remove(&id);
        return Err(error);
    }
    let timeout = if matches!(cmd, "check_cookies" | "check_session") { 60 } else { 15 };
    let response = tokio::time::timeout(std::time::Duration::from_secs(timeout), rx).await;
    state.pending.lock().unwrap().remove(&id);
    let response = response.map_err(|_| "引擎响应超时，请刷新任务状态后再操作".to_string())?
        .map_err(|_| "下载引擎已退出".to_string())?;
    if response.get("type").and_then(Value::as_str) == Some("error") {
        return Err(response.get("error").and_then(Value::as_str).unwrap_or("引擎错误").to_string());
    }
    Ok(response.get("data").cloned().unwrap_or(response))
}

#[tauri::command]
async fn alloc_job_id(state: State<'_, SidecarState>) -> Result<u64, String> { Ok(state.alloc_id()) }

#[tauri::command]
async fn ping_sidecar(state: State<'_, SidecarState>) -> Result<Value, String> {
    request(&state, "ping", Value::Null).await
}

#[tauri::command]
async fn start_parse(app: AppHandle, job_id: u64, url: String, cookies_from_browser: Option<String>, state: State<'_, SidecarState>) -> Result<(), String> {
    let mut args = serde_json::json!({"url":url,"cookies_from_browser":cookies_from_browser});
    if cookies_from_browser.as_deref() == Some("bilio") { auth::ensure_verified(&app).await?; }
    app.state::<auth::AuthState>().bind(&mut args)?;
    send(&state, job_id, "parse", args)
}

#[tauri::command]
async fn check_cookies(app: AppHandle, cookies_from_browser: Option<String>, state: State<'_, SidecarState>) -> Result<Value, String> {
    if cookies_from_browser.as_deref() == Some("bilio") { return Ok(app.state::<auth::AuthState>().status()); }
    request(&state, "check_cookies", serde_json::json!({"cookies_from_browser":cookies_from_browser})).await
}

#[tauri::command]
async fn task_command(app: AppHandle, command: String, mut args: Value, state: State<'_, SidecarState>) -> Result<Value, String> {
    if !matches!(command.as_str(), "download" | "tasks" | "clear_finished" | "remove_finished" | "cancel" | "cancel_batch" | "pause" | "resume" | "set_concurrency" | "temp_info" | "clear_temp" | "storage_status" | "relocate") {
        return Err("不支持的任务命令".into());
    }
    if command == "download" {
        if args.get("cookies_from_browser").and_then(Value::as_str) == Some("bilio") { auth::ensure_verified(&app).await?; }
        app.state::<auth::AuthState>().bind(&mut args)?;
    }
    if command == "resume" {
        let id = args.get("job_id").and_then(Value::as_u64).ok_or("缺少任务 ID")?;
        let snapshot = request(&state, "tasks", serde_json::json!({})).await?;
        let task = snapshot.as_array().and_then(|items| items.iter().find(|t| t["jobId"].as_u64() == Some(id))).ok_or("未找到任务")?;
        let mut config = task["config"].clone();
        if config["cookies_from_browser"] == "bilio" { auth::ensure_verified(&app).await?; }
        app.state::<auth::AuthState>().bind_resume(&mut config)?;
        args = serde_json::json!({"job_id":id,"auth_session_id":config["auth_session_id"],"account_ref":config["account_ref"]});
    }
    request(&state, &command, args).await
}

#[tauri::command]
async fn restart_sidecar(app: AppHandle, state: State<'_, SidecarState>) -> Result<(), String> {
    spawn_sidecar(&app, &state)?;
    auth::restore_engine(&app).await
}

#[tauri::command]
async fn choose_download_directory(app: AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().set_title("选择下载目录").blocking_pick_folder()
            .map(|path| path.into_path().map(|p| p.to_string_lossy().into_owned()).map_err(|e| e.to_string()))
            .transpose()
    }).await.map_err(|e| e.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let handler: fn(tauri::ipc::Invoke<tauri::Wry>) -> bool = tauri::generate_handler![alloc_job_id, ping_sidecar, start_parse, check_cookies, task_command, restart_sidecar, choose_download_directory, confirm_exit, diagnostic_report, export_diagnostics, check_update,
        auth::auth_status, auth::auth_login, auth::auth_logout, auth::auth_initialize, auth::auth_refresh, auth::auth_set_persistence];
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init()).plugin(tauri_plugin_shell::init())
        .manage(SidecarState::new())
        .manage(auth::AuthState::new())
        .manage(ExitPermission(AtomicBool::new(false)))
        .on_menu_event(|app, event| {
            if event.id().as_ref() == "bilio.request-quit" {
                let _ = app.emit_to("main", "app://quit-requested", ());
            }
        })
        .on_window_event(|window, event| {
            if window.label() == "main" && !window.app_handle().state::<ExitPermission>().0.load(Ordering::SeqCst) {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.app_handle().emit_to("main", "app://quit-requested", ());
                }
            }
        })
        .setup(|app| {
            #[cfg(target_os = "macos")]
            install_guarded_menu(app.handle())?;
            let state: State<SidecarState> = app.state();
            if let Err(error) = spawn_sidecar(app.handle(), &state) {
                eprintln!("[sidecar startup] {error}");
            }
            Ok(())
        })
        .invoke_handler(move |invoke: tauri::ipc::Invoke<tauri::Wry>| {
            let source = invoke.message.webview();
            if source.label() != "main" || !source.url().is_ok_and(|url| auth::main_origin(&url)) {
                invoke.resolver.reject("此窗口无权调用本机命令");
                return true;
            }
            handler(invoke)
        })
        .build(tauri::generate_context!()).expect("failed to build Bilio");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = &event {
            if !app.state::<ExitPermission>().0.load(Ordering::SeqCst) {
                api.prevent_exit();
                let _ = app.emit_to("main", "app://quit-requested", ());
            }
        }
        if let tauri::RunEvent::Exit = event {
            let state: State<SidecarState> = app.state();
            let child = state.child.lock().unwrap().take();
            if let Some(child) = child { let _ = child.kill(); }
        }
    });
}
