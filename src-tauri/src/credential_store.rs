//! Credentials go to Keychain; a non-secret policy file makes logout fail closed.
use std::{fs, io::Write, path::PathBuf};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Serialize, Deserialize)]
pub struct SavedSession {
    pub version: u32,
    pub account_ref: String,
    pub cookies: Value,
}

pub trait Secrets {
    fn load(&self) -> Result<Option<Vec<u8>>, String>;
    fn save(&self, bytes: &[u8]) -> Result<(), String>;
    fn delete(&self) -> Result<(), String>;
}

pub struct Keychain;
const SERVICE: &str = "com.bilio.desktop.session";
const ACCOUNT: &str = "bilibili-current";

#[cfg(target_os = "macos")]
impl Secrets for Keychain {
    fn load(&self) -> Result<Option<Vec<u8>>, String> {
        security_framework::passwords::get_generic_password(SERVICE, ACCOUNT).map(Some).or_else(|error| {
            if error.code() == -25300 { Ok(None) } else { Err("无法读取系统钥匙串，请检查授权后重试；登录数据未被删除".into()) }
        })
    }
    fn save(&self, bytes: &[u8]) -> Result<(), String> {
        security_framework::passwords::set_generic_password(SERVICE, ACCOUNT, bytes)
            .map_err(|_| "系统钥匙串未能保存登录；仍可使用本次会话，未写入明文文件".into())
    }
    fn delete(&self) -> Result<(), String> {
        security_framework::passwords::delete_generic_password(SERVICE, ACCOUNT).or_else(|error| {
            if error.code() == -25300 { Ok(()) } else { Err(error) }
        }).map_err(|_| "已禁用自动恢复，但无法删除钥匙串中的旧登录项；请在钥匙串访问中检查 Bilio 项目".into())
    }
}

#[cfg(not(target_os = "macos"))]
impl Secrets for Keychain {
    fn load(&self) -> Result<Option<Vec<u8>>, String> { Err("当前平台尚未实现安全登录存储".into()) }
    fn save(&self, _: &[u8]) -> Result<(), String> { Err("当前平台尚未实现安全登录存储".into()) }
    fn delete(&self) -> Result<(), String> { Err("当前平台尚未实现安全登录存储".into()) }
}

pub struct Vault<S: Secrets> { pub secrets: S, pub policy: PathBuf }

#[derive(Serialize, Deserialize)]
struct Policy { version: u32, restore_allowed: bool }

impl<S: Secrets> Vault<S> {
    pub fn disable_restore(&self) -> Result<(), String> { self.write_policy(false) }
    fn allow_restore(&self) -> Result<bool, String> {
        let bytes = match fs::read(&self.policy) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(_) => return Err("无法读取登录偏好；已暂停恢复，请检查应用数据目录".into()),
        };
        let policy: Policy = serde_json::from_slice(&bytes).map_err(|_| "登录偏好损坏；未自动读取已保存凭据".to_string())?;
        if policy.version != 1 { return Err("登录偏好版本不受支持".into()); }
        Ok(policy.restore_allowed)
    }
    fn write_policy(&self, allowed: bool) -> Result<(), String> {
        let parent = self.policy.parent().ok_or("无效的登录偏好路径")?;
        fs::create_dir_all(parent).map_err(|_| "无法写入登录偏好".to_string())?;
        let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
        let temporary = parent.join(format!(".session-policy-{unique}.tmp"));
        let result = (|| -> Result<(), std::io::Error> {
            let mut options = fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
            let mut file = options.open(&temporary)?;
            file.write_all(&serde_json::to_vec(&Policy { version:1, restore_allowed:allowed })?)?;
            file.sync_all()?;
            fs::rename(&temporary, &self.policy)?;
            Ok(())
        })();
        if result.is_err() { let _ = fs::remove_file(&temporary); }
        result.map_err(|_| "无法保存登录偏好，操作未完成；请检查目录或磁盘空间".into())
    }
    pub fn load(&self) -> Result<Option<SavedSession>, String> {
        if !self.allow_restore()? { return Ok(None); }
        let Some(bytes) = self.secrets.load()? else { return Ok(None); };
        if bytes.len() > 512 * 1024 { return Err("已保存的登录数据异常，请重新登录".into()); }
        let session: SavedSession = serde_json::from_slice(&bytes).map_err(|_| "已保存的登录数据损坏，请重新登录".to_string())?;
        if session.version != 1 || !session.account_ref.starts_with("bili-") || !session.cookies.is_array() {
            return Err("已保存的登录数据不受支持，请重新登录".into());
        }
        Ok(Some(session))
    }
    pub fn save(&self, session: &SavedSession) -> Result<(), String> {
        let bytes = serde_json::to_vec(session).map_err(|_| "无法编码登录数据".to_string())?;
        if bytes.len() > 512 * 1024 { return Err("登录数据过大，未保存".into()); }
        // An interrupted save must never silently revive an older account.
        self.write_policy(false)?;
        self.secrets.save(&bytes)?;
        if let Err(error) = self.write_policy(true) { let _ = self.secrets.delete(); return Err(error); }
        Ok(())
    }
    pub fn forget(&self) -> Result<(), String> {
        self.write_policy(false)?;
        self.secrets.delete()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    #[derive(Default)]
    struct Fake { value: Mutex<Option<Vec<u8>>>, fail_save: bool, fail_delete: bool, fail_load: bool }
    impl Secrets for Fake {
        fn load(&self) -> Result<Option<Vec<u8>>, String> { if self.fail_load { return Err("denied".into()); } Ok(self.value.lock().unwrap().clone()) }
        fn save(&self, bytes: &[u8]) -> Result<(), String> { if self.fail_save { return Err("denied".into()); } *self.value.lock().unwrap()=Some(bytes.to_vec());Ok(()) }
        fn delete(&self) -> Result<(), String> { if self.fail_delete { return Err("denied".into()); } self.value.lock().unwrap().take();Ok(()) }
    }
    fn vault(fake: Fake) -> Vault<Fake> {
        let unique=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let sequence = NEXT.fetch_add(1, Ordering::Relaxed);
        Vault { secrets:fake, policy:std::env::temp_dir().join(format!("bilio-vault-test-{}-{unique}-{sequence}/policy.json",std::process::id())) }
    }
    fn session() -> SavedSession { SavedSession {version:1,account_ref:"bili-test".into(),cookies:serde_json::json!([{"value":"private-test-cookie"}])} }
    #[test]
    fn does_not_read_keychain_without_opt_in() { let store=vault(Fake {fail_load:true,..Default::default()});assert!(store.load().unwrap().is_none()); }
    #[test]
    fn save_and_restore_without_credentials_in_policy() {
        let store=vault(Fake::default());store.save(&session()).unwrap();
        assert_eq!(store.load().unwrap().unwrap().account_ref,"bili-test");
        let policy=fs::read_to_string(&store.policy).unwrap();assert!(!policy.contains("private-test-cookie"));
        store.forget().unwrap();assert!(store.load().unwrap().is_none());
        fs::remove_dir_all(store.policy.parent().unwrap()).unwrap();
    }
    #[test]
    fn deletion_failure_cannot_restore_old_session() {
        let store=vault(Fake {fail_delete:true,..Default::default()});store.save(&session()).unwrap();
        assert!(store.forget().is_err());assert!(store.load().unwrap().is_none());
        fs::remove_dir_all(store.policy.parent().unwrap()).unwrap();
    }
    #[test]
    fn failed_save_never_falls_back_to_plaintext() {
        let store=vault(Fake {fail_save:true,..Default::default()});assert!(store.save(&session()).is_err());
        assert!(store.load().unwrap().is_none());assert!(!fs::read_to_string(&store.policy).unwrap().contains("private-test-cookie"));
        fs::remove_dir_all(store.policy.parent().unwrap()).unwrap();
    }
    #[test]
    fn corrupt_policy_never_reads_keychain() {
        let store=vault(Fake {fail_load:true,..Default::default()});
        store.disable_restore().unwrap();fs::write(&store.policy,b"invalid").unwrap();
        assert!(store.load().err().unwrap().contains("偏好损坏"));
        fs::remove_dir_all(store.policy.parent().unwrap()).unwrap();
    }
    #[test]
    fn keychain_read_failure_preserves_saved_data() {
        let store=vault(Fake {fail_load:true,..Default::default()});store.save(&session()).unwrap();
        assert!(store.load().is_err());assert!(store.allow_restore().unwrap());
        assert!(store.secrets.value.lock().unwrap().is_some());
        fs::remove_dir_all(store.policy.parent().unwrap()).unwrap();
    }
    #[test]
    fn disable_restore_does_not_touch_keychain() {
        let store=vault(Fake {fail_load:true,fail_delete:true,..Default::default()});
        store.save(&session()).unwrap();store.disable_restore().unwrap();
        assert!(store.load().unwrap().is_none());assert!(store.secrets.value.lock().unwrap().is_some());
        #[cfg(unix)] { use std::os::unix::fs::PermissionsExt; assert_eq!(fs::metadata(&store.policy).unwrap().permissions().mode() & 0o777,0o600); }
        fs::remove_dir_all(store.policy.parent().unwrap()).unwrap();
    }
}
