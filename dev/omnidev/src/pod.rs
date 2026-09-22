//! A `Pod` = one isolated dev instance: its own state dir, ports, and the env
//! map injected into every supervised child.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

use crate::ports::Ports;
use crate::profile::Profile;

pub struct Pod {
    pub repo_root: PathBuf,
    pub dir: PathBuf,
    pub ports: Ports,
    pub vite_host: String,
    /// LAN origins to trust for device testing (`--trust-lan-origins`); empty
    /// otherwise. Fed to the server as `AGENTNEXUS_WS_ALLOWED_ORIGINS`.
    pub trusted_origins: Vec<String>,
    pub profile: Option<Profile>,
}

impl Pod {
    /// Create the pod directory tree (idempotent) and return the pod handle.
    /// Only agentnexus's own state is isolated (DB, artifacts, logs, config); the
    /// pod inherits your real home, credentials, and caches.
    pub fn create(
        repo_root: PathBuf,
        dir: PathBuf,
        ports: Ports,
        vite_host: String,
        trusted_origins: Vec<String>,
    ) -> Result<Pod> {
        Self::create_with_profile(repo_root, dir, ports, vite_host, trusted_origins, None)
    }

    pub fn create_with_profile(
        repo_root: PathBuf,
        dir: PathBuf,
        ports: Ports,
        vite_host: String,
        trusted_origins: Vec<String>,
        profile: Option<Profile>,
    ) -> Result<Pod> {
        // Keep the persisted state directory so existing pods retain their history.
        for sub in ["data/omnigent", "artifacts", "logs", "config"] {
            let p = dir.join(sub);
            std::fs::create_dir_all(&p)
                .with_context(|| format!("creating pod dir {}", p.display()))?;
        }
        let pod = Pod {
            repo_root,
            dir,
            ports,
            vite_host,
            trusted_origins,
            profile,
        };
        // Seed the pod's config from the developer's real one so it works out
        // of the box (keeps their providers). Best-effort: a copy failure just
        // starts the pod with an empty config, so warn rather than abort.
        if let Some(src) = real_config_path() {
            let dest = pod.config_dir().join("config.yaml");
            if let Err(e) = seed_config_file(&src, &dest) {
                eprintln!("omnidev: could not seed pod config: {e:#}");
            }
        }
        Ok(pod)
    }

    pub fn db_uri(&self) -> String {
        format!(
            "sqlite:///{}",
            self.dir.join("data/omnigent/chat.db").display()
        )
    }

    pub fn artifacts_dir(&self) -> PathBuf {
        self.dir.join("artifacts")
    }

    /// The pod's isolated config home, exposed to children as
    /// `AGENTNEXUS_CONFIG_HOME` so its `config.yaml` is separate from the
    /// developer's real `~/.agentnexus/config.yaml`.
    pub fn config_dir(&self) -> PathBuf {
        self.dir.join("config")
    }

    pub fn server_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.ports.server)
    }

    /// Clickable URLs for display. Terminals linkify `localhost` but often not
    /// a bare `127.0.0.1`. Functional uses (server bind, host `--server`,
    /// `AGENTNEXUS_URL`) stay on `127.0.0.1` so we don't accidentally target IPv6
    /// `localhost` (`::1`), where the server isn't listening.
    pub fn server_display_url(&self) -> String {
        format!("http://localhost:{}", self.ports.server)
    }

    pub fn vite_display_url(&self) -> String {
        format!("http://localhost:{}", self.ports.vite)
    }

    pub fn web_dir(&self) -> PathBuf {
        self.repo_root.join(
            self.profile
                .as_ref()
                .map(|profile| profile.web_dir.as_path())
                .unwrap_or_else(|| Path::new("web")),
        )
    }

    /// Whether web dependencies need preparation before Vite can start.
    /// Profiles select their web directory and dependency manifests.
    pub fn needs_web_prepare(&self) -> bool {
        let web = self.web_dir();
        let modules = web.join("node_modules");
        if !modules.is_dir() {
            return true;
        }
        let mtime = |p: PathBuf| std::fs::metadata(p).and_then(|m| m.modified()).ok();
        let Some(installed) = mtime(modules) else {
            return true;
        };
        // Reinstall if either manifest is newer than node_modules.
        let manifests = self
            .profile
            .as_ref()
            .map(|profile| {
                profile
                    .dependency_manifests
                    .iter()
                    .map(|path| web.join(path))
                    .collect::<Vec<_>>()
            })
            .unwrap_or_else(|| {
                vec![
                    self.repo_root.join("pnpm-lock.yaml"),
                    web.join("package.json"),
                ]
            });
        manifests
            .into_iter()
            .filter_map(mtime)
            .any(|t| t > installed)
    }

    /// Directory to watch for backend source changes.
    pub fn backend_dir(&self) -> PathBuf {
        self.repo_root.join(
            self.profile
                .as_ref()
                .map(|profile| profile.backend_dir.as_path())
                .unwrap_or_else(|| Path::new("agentnexus")),
        )
    }

    pub fn host_enabled(&self) -> bool {
        self.profile
            .as_ref()
            .map_or(true, |profile| profile.host.is_some())
    }

    pub fn log_file(&self, name: &str) -> PathBuf {
        self.dir.join("logs").join(format!("{name}.log"))
    }

    /// The env overrides applied on top of the inherited parent env for every
    /// child. We isolate agentnexus's own state — the DB, data dir, and config
    /// home — so concurrent pods don't share a database, pidfile, or
    /// `config.yaml`. The rest (real `HOME`, credentials, uv/pnpm caches) is
    /// inherited, since the agents agentnexus runs need it. `AGENTNEXUS_URL` is the
    /// seam `web/vite.config.ts` reads to point its proxy at this pod's backend;
    /// `AGENTNEXUS_CONFIG_HOME` is where the server/host/runner read `config.yaml`.
    pub fn env(&self) -> Vec<(String, String)> {
        let d = |p: &str| self.dir.join(p).display().to_string();
        let mut env = vec![
            ("AGENTNEXUS_DATA_DIR".into(), d("data/omnigent")),
            ("AGENTNEXUS_DATABASE_URI".into(), self.db_uri()),
            ("AGENTNEXUS_URL".into(), self.server_url()),
            (
                "AGENTNEXUS_CONFIG_HOME".into(),
                self.config_dir().display().to_string(),
            ),
        ];
        if let Some(allowed) = self.allowed_origins_env() {
            env.push(("AGENTNEXUS_WS_ALLOWED_ORIGINS".into(), allowed));
        }
        env
    }

    /// The `AGENTNEXUS_WS_ALLOWED_ORIGINS` value to inject, or `None` to leave it
    /// untouched. Merges the trusted LAN origins onto any value inherited from
    /// the parent environment (comma-separated, order-preserving, deduped) so a
    /// developer's own allowlist survives. Returns `None` when there are no LAN
    /// origins to add — then the parent's value (if any) simply passes through.
    fn allowed_origins_env(&self) -> Option<String> {
        if self.trusted_origins.is_empty() {
            return None;
        }
        let inherited = env_value("WS_ALLOWED_ORIGINS").unwrap_or_default();
        let inherited = inherited.to_string_lossy();
        let mut merged: Vec<String> = Vec::new();
        let parts = inherited
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .chain(self.trusted_origins.iter().cloned());
        for part in parts {
            if !merged.contains(&part) {
                merged.push(part);
            }
        }
        Some(merged.join(","))
    }
}

/// Remove a pod directory (for `--clean`). No-op if it does not exist.
pub fn clean(dir: &Path) -> Result<()> {
    if dir.exists() {
        std::fs::remove_dir_all(dir)
            .with_context(|| format!("removing pod dir {}", dir.display()))?;
    }
    Ok(())
}

/// The developer's real agentnexus `config.yaml` to seed a fresh pod from.
///
/// Uses the configured home or the default config path, with legacy reads until 2.0.
fn real_config_path() -> Option<PathBuf> {
    resolve_config_path(env_value("CONFIG_HOME"), std::env::var_os("HOME"))
}

fn resolve_config_path(config_home: Option<OsString>, user_home: Option<OsString>) -> Option<PathBuf> {
    let explicit = config_home.is_some();
    let home = match config_home {
        Some(h) if !h.is_empty() => PathBuf::from(h),
        _ => PathBuf::from(user_home.as_ref()?).join(".agentnexus"),
    };
    let path = home.join("config.yaml");
    if path.exists() {
        return Some(path);
    }
    if !explicit {
        let legacy = PathBuf::from(user_home?).join(".omnigent/config.yaml");
        return legacy.exists().then_some(legacy);
    }
    None
}

fn env_value(suffix: &str) -> Option<OsString> {
    env_value_from(suffix, |name| std::env::var_os(name))
}

fn env_value_from(suffix: &str, mut read: impl FnMut(&str) -> Option<OsString>) -> Option<OsString> {
    // Legacy prefixes are supported until 2.0; an explicitly empty new value wins.
    ["AGENTNEXUS_", "OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"]
        .iter()
        .find_map(|prefix| read(&format!("{prefix}{suffix}")))
}

/// Copy `src` to `dest`, but only when `dest` does not already exist — a normal
/// pod restart must not clobber config the developer edited inside the pod.
/// After `--clean` the whole pod dir is gone, so `dest` is absent and this
/// re-seeds.
fn seed_config_file(src: &Path, dest: &Path) -> Result<()> {
    if dest.exists() {
        return Ok(());
    }
    std::fs::copy(src, dest)
        .with_context(|| format!("seeding {} from {}", dest.display(), src.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // `real_config_path` reads process-global env; serialize the tests that
    // set it so parallel runs don't observe each other's overrides.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn tempdir() -> PathBuf {
        let unique = format!(
            "omnidev-pod-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let dir = std::env::temp_dir().join(unique);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn make_pod(pod_dir: PathBuf) -> Pod {
        Pod::create(
            tempdir(),
            pod_dir,
            Ports {
                server: 19191,
                vite: 19292,
            },
            "127.0.0.1".into(),
            Vec::new(),
        )
        .unwrap()
    }

    /// Point `AGENTNEXUS_CONFIG_HOME` at `home` for the duration of `f`, restoring
    /// the previous value afterwards. Serialized against other env-touching
    /// tests via `ENV_LOCK`.
    fn with_config_home<T>(home: &Path, f: impl FnOnce() -> T) -> T {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let prev = std::env::var_os("AGENTNEXUS_CONFIG_HOME");
        std::env::set_var("AGENTNEXUS_CONFIG_HOME", home);
        let out = f();
        match prev {
            Some(v) => std::env::set_var("AGENTNEXUS_CONFIG_HOME", v),
            None => std::env::remove_var("AGENTNEXUS_CONFIG_HOME"),
        }
        out
    }

    #[test]
    fn create_makes_config_dir() {
        let real = tempdir(); // empty config home -> nothing to seed
        let pod = with_config_home(&real, || make_pod(tempdir()));
        assert!(pod.config_dir().is_dir());
    }

    #[test]
    fn env_includes_config_home() {
        let real = tempdir();
        let pod = with_config_home(&real, || make_pod(tempdir()));
        let env = pod.env();
        let got = env
            .iter()
            .find(|(k, _)| k == "AGENTNEXUS_CONFIG_HOME")
            .map(|(_, v)| v.clone());
        assert_eq!(got, Some(pod.config_dir().display().to_string()));
    }

    #[test]
    fn create_seeds_pod_config_from_real() {
        let real = tempdir();
        std::fs::write(real.join("config.yaml"), "providers:\n  seeded: true\n").unwrap();

        let pod = with_config_home(&real, || make_pod(tempdir()));

        let seeded = std::fs::read_to_string(pod.config_dir().join("config.yaml")).unwrap();
        assert_eq!(seeded, "providers:\n  seeded: true\n");
    }

    #[test]
    fn create_skips_seed_when_real_config_absent() {
        let real = tempdir(); // no config.yaml inside
        let pod = with_config_home(&real, || make_pod(tempdir()));
        assert!(!pod.config_dir().join("config.yaml").exists());
    }

    #[test]
    fn seed_does_not_overwrite_existing() {
        let dir = tempdir();
        let src = dir.join("src.yaml");
        let dest = dir.join("dest.yaml");
        std::fs::write(&src, "from: real\n").unwrap();
        std::fs::write(&dest, "edited: in-pod\n").unwrap();

        seed_config_file(&src, &dest).unwrap();

        // Existing pod-local edits survive; the real config does not clobber them.
        assert_eq!(std::fs::read_to_string(&dest).unwrap(), "edited: in-pod\n");
    }

    #[test]
    fn real_config_path_honors_config_home() {
        let real = tempdir();
        std::fs::write(real.join("config.yaml"), "x: 1\n").unwrap();
        let got = with_config_home(&real, real_config_path);
        assert_eq!(got, Some(real.join("config.yaml")));
    }

    #[test]
    fn real_config_path_preserves_legacy_and_prefers_canonical_config() {
        let home = tempdir();
        std::fs::create_dir_all(home.join(".omnigent")).unwrap();
        std::fs::write(home.join(".omnigent/config.yaml"), "y: 2\n").unwrap();
        let resolve = |override_home| resolve_config_path(override_home, Some(home.clone().into()));
        assert_eq!(resolve(None), Some(home.join(".omnigent/config.yaml")));
        assert_eq!(resolve(Some(OsString::new())), None);
        std::fs::create_dir_all(home.join(".agentnexus")).unwrap();
        std::fs::write(home.join(".agentnexus/config.yaml"), "").unwrap();
        assert_eq!(resolve(None), Some(home.join(".agentnexus/config.yaml")));
    }

    #[test]
    fn env_prefix_precedence_preserves_explicit_empty_values() {
        let mut env = std::collections::HashMap::new();
        let suffix = "WS_ALLOWED_ORIGINS";
        for prefix in ["OMNIAGENTS_", "OMNIGENTS_", "OMNIGENT_", "AGENTNEXUS_"] {
            env.insert(format!("{prefix}{suffix}"), OsString::from(prefix));
            assert_eq!(
                env_value_from(suffix, |key| env.get(key).cloned()),
                Some(prefix.into())
            );
        }
        env.insert("AGENTNEXUS_WS_ALLOWED_ORIGINS".into(), OsString::new());
        assert_eq!(
            env_value_from(suffix, |key| env.get(key).cloned()),
            Some(OsString::new())
        );
    }

    #[test]
    fn backend_watch_path_is_canonical_but_persisted_data_path_is_retained() {
        let real = tempdir();
        let pod = with_config_home(&real, || make_pod(tempdir()));
        assert_eq!(pod.backend_dir(), pod.repo_root.join("agentnexus"));
        assert!(pod.dir.join("data/omnigent").is_dir());
        assert!(pod
            .env()
            .iter()
            .all(|(key, _)| key.starts_with("AGENTNEXUS_")));
    }
}
