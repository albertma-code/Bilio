//! Isolated official login page. Credentials only cross the native/sidecar pipe.
use std::sync::{atomic::{AtomicBool, AtomicU64, Ordering}, Mutex};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder};
use super::{request, SidecarState};
use crate::credential_store::{Vault, Keychain, SavedSession};
use sha2::{Digest, Sha256};

const LOGIN_WINDOW: &str = "bilibili-login";
struct Session { id: String, cookies: Value, status: Value, account_ref: String, verified: std::time::Instant }
pub struct AuthState {
    session: Mutex<Option<Session>>,
    generation: AtomicU64,
    open: AtomicBool,
    error: Mutex<Option<String>>,
    phase: Mutex<&'static str>,
    remembered: AtomicBool,
    initialized: AtomicBool,
    operation: tokio::sync::Mutex<()>,
}
impl AuthState {
    pub fn new() -> Self {
        Self { session: Mutex::new(None), generation: AtomicU64::new(0), open: AtomicBool::new(false), error: Mutex::new(None),
            phase: Mutex::new("initializing"), remembered: AtomicBool::new(false), initialized: AtomicBool::new(false), operation: tokio::sync::Mutex::new(()) }
    }
    pub fn status(&self) -> Value {
        let session = self.session.lock().unwrap();
        let mut result = session.as_ref().map(|s| s.status.clone()).unwrap_or(json!({
            "ok":true,"logged_in":false,"is_vip":false,"username":null,"vip_label":null,"error":null
        }));
        result["session_id"] = session.as_ref().map(|s| json!(s.id)).unwrap_or(Value::Null);
        result["login_open"] = json!(self.open.load(Ordering::SeqCst));
        result["persistence"] = json!(if self.remembered.load(Ordering::SeqCst) { "keychain" } else { "session" });
        result["account_ref"] = session.as_ref().map(|s| json!(s.account_ref)).unwrap_or(Value::Null);
        let phase = *self.phase.lock().unwrap();
        result["state"] = json!(phase);
        // Cached membership must never look verified after a failed network check.
        if phase != "verified" { result["logged_in"] = json!(false); result["is_vip"] = json!(false); }
        if let Some(object) = result.as_object_mut() { object.remove("account_id"); }
        if let Some(error) = self.error.lock().unwrap().as_ref() { result["error"] = json!(error); }
        result
    }
    pub fn bind(&self, args: &mut Value) -> Result<(), String> {
        if args.get("cookies_from_browser").and_then(Value::as_str) != Some("bilio") { return Ok(()); }
        let session = self.session.lock().unwrap();
        if *self.phase.lock().unwrap() != "verified" { return Err("账号尚未验证，请在设置中重新检查或登录".into()); }
        let session = session.as_ref().ok_or("请先在 Bilio 登录 B站")?;
        if let Some(expected) = args.get("auth_session_id").and_then(Value::as_str) {
            if expected != session.id { return Err("登录会话已变化，请重新解析后添加下载".into()); }
        }
        args["auth_session_id"] = json!(session.id);
        args["account_ref"] = json!(session.account_ref);
        Ok(())
    }
    pub fn bind_resume(&self, args: &mut Value) -> Result<(), String> {
        if args.get("cookies_from_browser").and_then(Value::as_str) != Some("bilio") { return Ok(()); }
        {
            let guard = self.session.lock().unwrap();
            let session = guard.as_ref().ok_or("请先登录原任务的 B站账号")?;
            let same_session = args.get("auth_session_id").and_then(Value::as_str) == Some(session.id.as_str());
            let same_account = args.get("account_ref").and_then(Value::as_str) == Some(session.account_ref.as_str());
            if !same_session && !same_account { return Err("当前账号与原任务不同或旧任务缺少账号身份，请重新解析或切回原账号".into()); }
            args["auth_session_id"] = json!(session.id);
        }
        // Reuse the normal verified-state fence after allowing a same-account revision.
        self.bind(args)
    }
}

fn vault(app: &AppHandle) -> Result<Vault<Keychain>, String> {
    Ok(Vault { secrets: Keychain, policy: app.path().app_data_dir().map_err(|_| "无法定位应用数据目录")?.join("session-policy.json") })
}
fn account_ref(status: &Value) -> Result<String, String> {
    let id = status.get("account_id").and_then(Value::as_str).filter(|id| !id.is_empty()).ok_or("无法确认账号身份，请重新登录")?;
    Ok(format!("bili-{:x}", Sha256::digest(format!("bilio-account:{id}").as_bytes())))
}

// Caller holds operation; generation checks fence late responses after logout/login.
async fn verify(app: &AppHandle, generation: u64, id: String, cookies: Value, expected: Option<String>) -> Result<(), String> {
    let auth = app.state::<AuthState>();
    let sidecar = app.state::<SidecarState>();
    let result = request(&sidecar, "check_session", json!({"cookies":cookies})).await;
    if auth.generation.load(Ordering::SeqCst) != generation { return Err("登录操作已取消".into()); }
    let status = match result {
        Ok(status) if status["ok"] == true => status,
        _ => {
            *auth.phase.lock().unwrap() = "offline";
            return Err("暂时无法验证 B站账号，请检查网络后重新检测；已保存的登录未被删除".into());
        }
    };
    if status["logged_in"] != true {
        *auth.phase.lock().unwrap() = "expired";
        return Err("B站登录已失效，请重新登录".into());
    }
    let identity = account_ref(&status).map_err(|error| { *auth.phase.lock().unwrap() = "expired"; error })?;
    if expected.is_some_and(|value| value != identity) {
        *auth.phase.lock().unwrap() = "expired";
        return Err("登录账号与保存的账号不一致，请重新登录".into());
    }
    if request(&sidecar, "set_session", json!({"session_id":id,"cookies":cookies})).await.is_err() {
        if auth.generation.load(Ordering::SeqCst) == generation { *auth.phase.lock().unwrap() = "offline"; }
        return Err("账号验证完成，但下载引擎未接受登录；请重启引擎后重新检测".into());
    }
    if auth.generation.load(Ordering::SeqCst) != generation {
        let _ = request(&sidecar, "clear_session", json!({"session_id":id})).await;
        return Err("登录操作已取消".into());
    }
    *auth.session.lock().unwrap() = Some(Session {id,cookies,status,account_ref:identity,verified:std::time::Instant::now()});
    *auth.phase.lock().unwrap() = "verified";
    *auth.error.lock().unwrap() = None;
    Ok(())
}

#[tauri::command]
pub async fn auth_initialize(app: AppHandle) -> Result<Value, String> {
    let auth = app.state::<AuthState>();
    let _guard = auth.operation.lock().await;
    if auth.initialized.swap(true, Ordering::SeqCst) { return Ok(auth.status()); }
    let generation = auth.generation.load(Ordering::SeqCst);
    *auth.phase.lock().unwrap() = "checking"; notify(&app);
    let saved = match vault(&app) {
        Ok(store) => match tauri::async_runtime::spawn_blocking(move || store.load()).await {
            Ok(result) => result, Err(_) => Err("读取登录存储失败，请重试".into()),
        },
        Err(error) => Err(error),
    };
    if auth.generation.load(Ordering::SeqCst) != generation { return Ok(auth.status()); }
    match saved {
        Ok(Some(saved)) => {
            auth.remembered.store(true, Ordering::SeqCst);
            // Retain credentials for an explicit retry if the network is unavailable.
            let id = app.state::<SidecarState>().alloc_id().to_string();
            *auth.session.lock().unwrap() = Some(Session {id:id.clone(),cookies:saved.cookies.clone(),status:json!({"ok":false}),account_ref:saved.account_ref.clone(),verified:std::time::Instant::now()});
            if let Err(error) = verify(&app,generation,id,saved.cookies,Some(saved.account_ref)).await {
                if auth.generation.load(Ordering::SeqCst) == generation { *auth.error.lock().unwrap() = Some(error); }
            }
        }
        Ok(None) => { *auth.phase.lock().unwrap() = "signed_out"; }
        Err(error) => { *auth.phase.lock().unwrap() = "offline"; *auth.error.lock().unwrap() = Some(error); }
    }
    notify(&app);
    Ok(auth.status())
}

#[tauri::command]
pub async fn auth_refresh(app: AppHandle) -> Result<Value, String> {
    let auth = app.state::<AuthState>();
    let _guard = auth.operation.lock().await;
    let generation = auth.generation.load(Ordering::SeqCst);
    let saved = auth.session.lock().unwrap().as_ref().map(|s| (s.id.clone(),s.cookies.clone(),s.account_ref.clone()));
    if let Some((id,cookies,identity)) = saved {
        *auth.phase.lock().unwrap() = "checking"; notify(&app);
        if let Err(error) = verify(&app,generation,id,cookies,Some(identity)).await {
            if auth.generation.load(Ordering::SeqCst) == generation { *auth.error.lock().unwrap() = Some(error); }
        }
    } else if *auth.phase.lock().unwrap() == "offline" {
        auth.initialized.store(false, Ordering::SeqCst);
        drop(_guard);
        return auth_initialize(app.clone()).await;
    }
    notify(&app); Ok(auth.status())
}

pub async fn ensure_verified(app: &AppHandle) -> Result<(), String> {
    let fresh = {
        let auth = app.state::<AuthState>();
        let session = auth.session.lock().unwrap();
        *auth.phase.lock().unwrap() == "verified" && session.as_ref().is_some_and(|s| s.verified.elapsed().as_secs() < 60)
    };
    if !fresh { auth_refresh(app.clone()).await?; }
    if app.state::<AuthState>().status()["logged_in"] == true { Ok(()) } else { Err("账号未验证，请检查网络或重新登录；未切换为匿名画质".into()) }
}

#[tauri::command]
pub async fn auth_set_persistence(app: AppHandle, remember: bool, session_id: String) -> Result<Value, String> {
    let auth = app.state::<AuthState>();
    let _guard = auth.operation.lock().await;
    let generation = auth.generation.load(Ordering::SeqCst);
    let saved = {
        let session = auth.session.lock().unwrap();
        let s = session.as_ref().filter(|s| s.id == session_id).ok_or("登录会话已变化，请重新确认")?;
        if remember && *auth.phase.lock().unwrap() != "verified" { return Err("请先验证账号再保存登录".into()); }
        SavedSession {version:1, account_ref:s.account_ref.clone(), cookies:s.cookies.clone()}
    };
    let store = vault(&app)?;
    let result = tauri::async_runtime::spawn_blocking(move || if remember { store.save(&saved) } else { store.forget() }).await.map_err(|_| "登录存储操作失败")?;
    auth.remembered.store(result.is_ok() && remember, Ordering::SeqCst);
    if auth.generation.load(Ordering::SeqCst) != generation {
        let store = vault(&app)?;
        let _ = tauri::async_runtime::spawn_blocking(move || store.forget()).await;
        auth.remembered.store(false, Ordering::SeqCst);
        return Err("登录已变化，未保留登录".into());
    }
    notify(&app); result?; Ok(auth.status())
}

pub fn main_origin(url: &tauri::Url) -> bool {
    (url.scheme() == "tauri" && url.host_str() == Some("localhost")) ||
    (matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost")) ||
    (cfg!(debug_assertions) && url.scheme() == "http" && matches!(url.host_str(), Some("localhost" | "127.0.0.1")) && url.port() == Some(1420))
}
fn bili_domain(domain: &str) -> bool {
    let domain = domain.trim_start_matches('.');
    domain == "bilibili.com" || domain.ends_with(".bilibili.com")
}
fn login_navigation(url: &tauri::Url) -> bool {
    url.as_str() == "about:blank" || (url.scheme() == "https" && url.host_str().is_some_and(bili_domain))
}
fn notify(app: &AppHandle) {
    let state = app.state::<AuthState>();
    let _ = app.emit_to("main", "auth://changed", state.status());
}

#[tauri::command]
pub async fn auth_status(app: AppHandle) -> Result<Value, String> { Ok(app.state::<AuthState>().status()) }

#[tauri::command]
pub async fn auth_login(app: AppHandle) -> Result<Value, String> {
    if let Some(window) = app.get_webview_window(LOGIN_WINDOW) {
        let _ = window.show(); let _ = window.set_focus();
        return Ok(app.state::<AuthState>().status());
    }
    let state = app.state::<AuthState>();
    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    let _guard = state.operation.lock().await;
    state.initialized.store(true, Ordering::SeqCst);
    let store = vault(&app)?;
    tauri::async_runtime::spawn_blocking(move || store.disable_restore()).await.map_err(|_| "无法禁用旧登录恢复")??;
    if state.remembered.load(Ordering::SeqCst) {
        let store = vault(&app)?;
        tauri::async_runtime::spawn_blocking(move || store.forget()).await.map_err(|_| "无法清除旧登录")??;
        state.remembered.store(false, Ordering::SeqCst);
    }
    if state.generation.load(Ordering::SeqCst) != generation { return Err("登录操作已取消".into()); }
    *state.phase.lock().unwrap() = "waiting_scan";
    state.open.store(true, Ordering::SeqCst);
    *state.error.lock().unwrap() = None;
    let window = WebviewWindowBuilder::new(&app, LOGIN_WINDOW,
        WebviewUrl::External("https://passport.bilibili.com/login".parse().unwrap()))
        .title("登录 B站 · passport.bilibili.com")
        .inner_size(1080.0, 720.0).min_inner_size(720.0, 540.0)
        .incognito(true).devtools(false)
        .on_navigation(login_navigation)
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
        .build();
    let window = match window {
        Ok(window) => window,
        Err(_) => { state.open.store(false, Ordering::SeqCst); *state.phase.lock().unwrap() = "signed_out"; return Err("无法打开 B站登录窗口，请重试".into()); }
    };
    let on_close = app.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed) {
            let state = on_close.state::<AuthState>();
            if state.open.swap(false, Ordering::SeqCst) {
                state.generation.fetch_add(1, Ordering::SeqCst);
                let has_session = state.session.lock().unwrap().is_some();
                *state.phase.lock().unwrap() = if has_session { "offline" } else { "signed_out" };
                notify(&on_close);
            }
        }
    });
    let _ = window.set_focus();
    notify(&app);
    let poll_app = app.clone();
    tauri::async_runtime::spawn(async move { poll_login(poll_app, generation).await; });
    Ok(state.status())
}

async fn poll_login(app: AppHandle, generation: u64) {
    let start = std::time::Instant::now();
    let id = app.state::<SidecarState>().alloc_id().to_string();
    while start.elapsed().as_secs() < 300 {
        let auth = app.state::<AuthState>();
        if auth.generation.load(Ordering::SeqCst) != generation || !auth.open.load(Ordering::SeqCst) { return; }
        let Some(window) = app.get_webview_window(LOGIN_WINDOW) else { return; };
        let cookies = tauri::async_runtime::spawn_blocking(move || window.cookies()).await;
        let cookies = match cookies { Ok(Ok(cookies)) => cookies, _ => {
            tokio::time::sleep(std::time::Duration::from_secs(2)).await; continue;
        }};
        let cookies: Vec<Value> = cookies.into_iter().filter(|cookie| bili_domain(cookie.domain().unwrap_or("")))
            .map(|cookie| json!({"name":cookie.name(),"value":cookie.value(),"domain":cookie.domain(),
                "path":cookie.path().unwrap_or("/"),"secure":cookie.secure().unwrap_or(true),
                "expires":cookie.expires_datetime().map(|date| date.unix_timestamp())})).collect();
        if cookies.iter().any(|c| c["name"] == "SESSDATA" && c["value"].as_str().is_some_and(|v| !v.is_empty())) {
            let _guard = auth.operation.lock().await;
            if auth.generation.load(Ordering::SeqCst) != generation { return; }
            let verified = verify(&app, generation, id.clone(), json!(cookies), None).await;
            if verified.is_ok() {
                auth.open.store(false, Ordering::SeqCst);
                notify(&app);
                if let Some(window) = app.get_webview_window(LOGIN_WINDOW) { let _ = window.close(); }
                if let Some(main) = app.get_webview_window("main") { let _ = main.set_focus(); }
                return;
            }
            if auth.generation.load(Ordering::SeqCst) != generation { return; }
            *auth.error.lock().unwrap() = verified.err();
            notify(&app);
            drop(_guard);
            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
        }
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    }
    let state = app.state::<AuthState>();
    if state.generation.load(Ordering::SeqCst) == generation {
        state.open.store(false, Ordering::SeqCst);
        *state.phase.lock().unwrap() = "signed_out";
        *state.error.lock().unwrap() = Some("登录等待超时，请重新打开登录窗口".into());
        if let Some(window) = app.get_webview_window(LOGIN_WINDOW) { let _ = window.close(); }
        notify(&app);
    }
}

#[tauri::command]
pub async fn auth_logout(app: AppHandle, sidecar: State<'_, SidecarState>) -> Result<Value, String> {
    let state = app.state::<AuthState>();
    state.generation.fetch_add(1, Ordering::SeqCst);
    state.open.store(false, Ordering::SeqCst);
    state.session.lock().unwrap().take();
    *state.phase.lock().unwrap() = "signed_out";
    *state.error.lock().unwrap() = None;
    if let Some(window) = app.get_webview_window(LOGIN_WINDOW) { let _ = window.close(); }
    let _guard = state.operation.lock().await;
    // A restore already in flight may have retained a retry copy before it saw
    // the changed generation. Clear again after all earlier operations finish.
    state.session.lock().unwrap().take();
    *state.phase.lock().unwrap() = "signed_out";
    let store = vault(&app)?;
    let forgotten = tauri::async_runtime::spawn_blocking(move || store.forget()).await;
    state.remembered.store(false, Ordering::SeqCst);
    // Clear native memory even if the engine is unavailable; its exit loses its own copy.
    let result = request(&sidecar, "clear_session", json!({})).await;
    let warning = match forgotten { Ok(Err(error)) => Some(error), Err(_) => Some("未能完成登录存储清理，请重试退出".into()), _ => None };
    *state.error.lock().unwrap() = warning.or_else(|| result.err().map(|_| "本机登录已退出，下载引擎未响应；请重启引擎".into()));
    notify(&app);
    Ok(state.status())
}

pub async fn restore_engine(app: &AppHandle) -> Result<(), String> {
    let auth = app.state::<AuthState>();
    let _guard = auth.operation.lock().await;
    let session = auth.session.lock().unwrap().as_ref().map(|s| (s.id.clone(), s.cookies.clone()));
    if let Some((id, cookies)) = session {
        request(&app.state::<SidecarState>(), "set_session", json!({"session_id":id,"cookies":cookies})).await?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_official_https_login_navigation() {
        for url in ["https://passport.bilibili.com/login", "https://www.bilibili.com/"] { assert!(login_navigation(&url.parse().unwrap())); }
        for url in ["http://passport.bilibili.com/login", "https://bilibili.com.evil.test/", "https://evilbilibili.com/", "file:///tmp/test", "tauri://localhost/"] { assert!(!login_navigation(&url.parse().unwrap())); }
    }
    #[test]
    fn remote_origin_cannot_invoke_native_commands() {
        assert!(main_origin(&"tauri://localhost/".parse().unwrap()));
        assert!(!main_origin(&"https://passport.bilibili.com/".parse().unwrap()));
        assert!(!main_origin(&"https://localhost.evil.test/".parse().unwrap()));
    }
    #[test]
    fn frontend_status_never_contains_credentials() {
        let state = AuthState::new();
        *state.session.lock().unwrap() = Some(Session { id: "test-session".into(), cookies: json!([{"value":"private-test-value"}]), status:json!({"logged_in":true,"account_id":"123"}), account_ref:"bili-test".into(), verified:std::time::Instant::now() });
        assert!(!state.status().to_string().contains("private-test-value"));
        assert!(state.status().get("account_id").is_none());
        let mut task = json!({"cookies_from_browser":"bilio","auth_session_id":"old-session"});
        assert!(state.bind(&mut task).is_err());
    }
    #[test]
    fn offline_never_claims_membership() {
        let state = AuthState::new();
        *state.session.lock().unwrap() = Some(Session {id:"s".into(),cookies:json!([]),status:json!({"logged_in":true,"is_vip":true}),account_ref:"bili-test".into(),verified:std::time::Instant::now()});
        *state.phase.lock().unwrap() = "offline";
        assert_eq!(state.status()["logged_in"],false);
        assert_eq!(state.status()["is_vip"],false);
        assert!(state.bind(&mut json!({"cookies_from_browser":"bilio"})).is_err());
    }
    #[test]
    fn account_identity_is_stable_and_opaque() {
        let first = account_ref(&json!({"account_id":"123","username":"old"})).unwrap();
        assert_eq!(first, account_ref(&json!({"account_id":"123","username":"new"})).unwrap());
        assert_ne!(first, account_ref(&json!({"account_id":"124"})).unwrap());
        assert!(account_ref(&json!({})).is_err());
    }
    #[test]
    fn explicit_resume_allows_same_account_but_never_different_account() {
        let state=AuthState::new();
        *state.phase.lock().unwrap()="verified";
        *state.session.lock().unwrap()=Some(Session{id:"new".into(),cookies:json!([]),status:json!({"logged_in":true}),account_ref:"bili-one".into(),verified:std::time::Instant::now()});
        let mut same=json!({"cookies_from_browser":"bilio","auth_session_id":"old","account_ref":"bili-one"});
        assert!(state.bind(&mut same).is_err());
        assert!(state.bind_resume(&mut same).is_ok());assert_eq!(same["auth_session_id"],"new");
        let mut other=json!({"cookies_from_browser":"bilio","auth_session_id":"old","account_ref":"bili-other"});
        assert!(state.bind_resume(&mut other).is_err());
        let mut legacy=json!({"cookies_from_browser":"bilio","auth_session_id":"old"});
        assert!(state.bind_resume(&mut legacy).is_err());
    }
}
