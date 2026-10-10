//! Optional native Supabase backend. The CLI owns services; Forklift owns the
//! private home, stopped snapshots and journaled source/state replacement.
use crate::lifecycle::{Error, Result, fail, validate_name};
use crate::paired::{inventory, read};
use crate::project::{Config, Project, checked_path, copy_item, layout, snapshot, validate};
use crate::storage::{atomic_json, copy_tree, directory, private_dir, random_id, sync_dir};
use crate::supabase_runtime::Runtime;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::Read,
    os::{
        fd::AsRawFd,
        unix::{
            fs::{MetadataExt, OpenOptionsExt},
            process::{CommandExt, ExitStatusExt},
        },
    },
    path::{Path, PathBuf},
    process::Command,
};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Binding {
    format: u32,
    root: PathBuf,
    state: PathBuf,
    cli: PathBuf,
    pg_bin: PathBuf,
    config: Config,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    format: u32,
    runtime: Value,
    files: BTreeMap<String, String>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    format: u32,
    name: String,
    starting: bool,
}

pub fn selected(root: &Path) -> Result<bool> {
    let (root, _, admin) = layout(root)?;
    if admin.join("forklift-id.json").try_exists()? {
        let (_, state) = Project::state(&root, false)?;
        if state.join("supabase.json").try_exists()? {
            return Ok(true);
        }
        if state.join("binding.json").try_exists()? {
            return Ok(false);
        }
    }
    if root.join(".forklift.json").try_exists()? {
        let c: Config = read(&root.join(".forklift.json"))?;
        if let Some(backend) = c.backend {
            return match backend.as_str() {
                "supabase" => Ok(true),
                "postgres" => Ok(false),
                _ => fail("project_config", "backend must be postgres or supabase"),
            };
        }
    }
    Ok(root.join("supabase/config.toml").is_file())
}
fn fault(stage: &str) {
    #[cfg(feature = "test-failpoints")]
    if std::env::var("FORKLIFT_SUPABASE_TEST_FAILPOINT").as_deref() == Ok(stage) {
        std::process::exit(99);
    }
    let _ = stage;
}
fn executable(name: &str) -> Result<PathBuf> {
    let p = Path::new(name);
    if p.components().count() > 1 {
        return Ok(fs::canonicalize(p)?);
    }
    for dir in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
        let p = dir.join(name);
        if p.is_file() {
            return Ok(fs::canonicalize(p)?);
        }
    }
    fail(
        "supabase_installation",
        "install Supabase CLI 2.120.0 or set FORKLIFT_SUPABASE_BIN to its executable",
    )
}
fn default_config(root: &Path) -> Result<Config> {
    // Deliberately select conventional application paths, never dotenv or runtime data.
    let candidates = [
        "src",
        "app",
        "pages",
        "components",
        "lib",
        "utils",
        "styles",
        "config",
        "types",
        "public",
        "scripts",
        "tests",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "tsconfig.json",
        "next.config.ts",
        "next.config.js",
        "next.config.mjs",
        "postcss.config.mjs",
        "tailwind.config.ts",
        "components.json",
        "supabase/config.toml",
        "supabase/migrations",
        "supabase/seed.sql",
        "supabase/functions",
    ];
    let c = Config {
        format: 1,
        source: candidates
            .iter()
            .filter(|p| root.join(p).exists())
            .map(PathBuf::from)
            .collect(),
        connection_env: "DATABASE_URL".into(),
        backend: Some("supabase".into()),
        supabase_env: BTreeMap::new(),
        database: "postgres".into(),
        tcp: false,
        commands: BTreeMap::new(),
    };
    validate(&c)?;
    Ok(c)
}
fn env_name(s: &str) -> bool {
    !s.is_empty()
        && s.bytes()
            .enumerate()
            .all(|(i, b)| b == b'_' || b.is_ascii_alphabetic() || i > 0 && b.is_ascii_digit())
}
fn validate_config(c: &Config) -> Result<()> {
    validate(c)?;
    if c.backend.as_deref().is_some_and(|b| b != "supabase") {
        return fail(
            "project_config",
            "Supabase binding requires backend supabase",
        );
    }
    if c.database != "postgres" {
        return fail("project_config", "Supabase uses its postgres database");
    }
    if !c
        .source
        .iter()
        .any(|p| p == Path::new("supabase/config.toml"))
        || c.source
            .iter()
            .any(|p| p == Path::new("supabase") || p.starts_with("supabase/.temp"))
    {
        return fail(
            "project_config",
            "select supabase/config.toml and migrations/functions individually; exclude supabase/.temp",
        );
    }
    if [
        "NEXT_PUBLIC_",
        "VITE_",
        "PUBLIC_",
        "NUXT_PUBLIC_",
        "EXPO_PUBLIC_",
        "REACT_APP_",
    ]
    .iter()
    .any(|p| c.connection_env.starts_with(p))
    {
        return fail(
            "project_config",
            "database credentials cannot use a public client environment variable",
        );
    }
    for (from, to) in &c.supabase_env {
        if ![
            "API_URL",
            "DB_URL",
            "ANON_KEY",
            "PUBLISHABLE_KEY",
            "SERVICE_ROLE_KEY",
            "SECRET_KEY",
        ]
        .contains(&from.as_str())
            || !env_name(to)
            || [
                "PATH",
                "HOME",
                "PG_BIN",
                "SUPABASE_HOME",
                "LD_PRELOAD",
                "DYLD_INSERT_LIBRARIES",
            ]
            .contains(&to.as_str())
            || ((from == "SERVICE_ROLE_KEY" || from == "SECRET_KEY" || from == "DB_URL")
                && (to.starts_with("NEXT_PUBLIC_")
                    || to.starts_with("VITE_")
                    || to.starts_with("PUBLIC_")
                    || to.starts_with("NUXT_PUBLIC_")
                    || to.starts_with("EXPO_PUBLIC_")
                    || to.starts_with("REACT_APP_")))
        {
            return fail(
                "project_config",
                "invalid Supabase environment mapping; secret keys cannot use public client variable names",
            );
        }
    }
    Ok(())
}
// Hash large database/storage files in bounded memory. Metadata is a single hash per
// file rather than embedding data. Links and live process files are never captured.
fn tree_hash(root: &Path) -> Result<BTreeMap<String, String>> {
    fn walk(root: &Path, path: &Path, out: &mut BTreeMap<String, String>) -> Result<()> {
        if out.len() >= 100_000 {
            return fail("supabase_integrity", "snapshot exceeds 100000 entries");
        }
        let m = fs::symlink_metadata(path)?;
        if m.uid() != unsafe { libc::geteuid() } || m.mode() & 0o022 != 0 {
            return fail("unsafe_path", "snapshot path is not privately owned");
        }
        let name = path
            .strip_prefix(root)
            .unwrap()
            .to_str()
            .ok_or_else(|| Error::new("unsafe_path", "non-UTF8 snapshot path"))?
            .to_owned();
        if m.is_dir() {
            out.insert(name, format!("directory:{:o}", m.mode() & 0o777));
            for e in fs::read_dir(path)? {
                walk(root, &e?.path(), out)?;
            }
        } else if m.is_file() && m.nlink() == 1 {
            if matches!(
                path.file_name().and_then(|n| n.to_str()),
                Some("postmaster.pid" | "owner.json")
            ) {
                return fail("not_stopped", "Supabase still has a process ownership file");
            }
            let mut f = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(path)?;
            let mut h = Sha256::new();
            let mut buf = [0; 131072];
            loop {
                let n = f.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                h.update(&buf[..n]);
            }
            out.insert(
                name,
                format!("file:{:o}:{:x}", m.mode() & 0o777, h.finalize()),
            );
        } else {
            return fail("unsafe_path", "snapshot contains a link or special file");
        }
        Ok(())
    }
    let mut out = BTreeMap::new();
    walk(root, root, &mut out)?;
    Ok(out)
}

pub struct SupabaseProject {
    binding: Binding,
    gate: File,
    full: bool,
}
impl SupabaseProject {
    fn check_owned(&self) -> Result<()> {
        crate::retention::safe_directory(&self.binding.state)?;
        if fs::metadata(&self.binding.state)?.mode() & 0o077 != 0 {
            return fail(
                "unsafe_path",
                "Supabase state directory must remain private",
            );
        }
        for relative in [
            "home",
            "home/stacks",
            "runtime",
            "runtime/supabase",
            "runtime/supabase/.temp",
            "runtime/supabase/.temp/stack-uploads",
            "captures",
        ] {
            let p = checked_path(&self.binding.state, Path::new(relative))?;
            if p.exists() {
                crate::retention::safe_directory(&p)?;
            }
        }
        Ok(())
    }
    fn runtime(&self) -> Result<Runtime> {
        self.check_owned()?;
        Runtime::new(
            &self.binding.cli,
            &self.binding.state.join("home"),
            &self.binding.state.join("runtime"),
            &self.binding.pg_bin,
        )
    }
    fn lock(state: &Path) -> Result<File> {
        let gate = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(state.join("gate.lock"))?;
        if !gate.metadata()?.is_file() || gate.metadata()?.nlink() != 1 {
            return fail("unsafe_path", "unsafe Supabase gate");
        }
        if unsafe { libc::flock(gate.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return fail(
                "project_locked",
                "a managed command holds this Supabase workspace; stop it before capture/restore",
            );
        }
        Ok(gate)
    }
    pub fn setup(root: &Path, pg: Option<&Path>, full: bool) -> Result<Self> {
        let (root, state) = Project::state(root, true)?;
        if state.join("binding.json").exists() {
            return fail(
                "project_config",
                "worktree already uses PostgreSQL; use a fresh worktree to change backend",
            );
        }
        if !state.exists() {
            private_dir(&state)?;
            sync_dir(state.parent().unwrap())?;
        }
        directory(&state)?;
        crate::retention::safe_directory(&state)?;
        let gate = Self::lock(&state)?;
        let path = state.join("supabase.json");
        let binding = if path.exists() {
            read::<Binding>(&path)?
        } else {
            let config_path = root.join(".forklift.json");
            let config = if config_path.exists() {
                read(&config_path)?
            } else {
                let c = default_config(&root)?;
                atomic_json(&config_path, &c)?;
                c
            };
            validate_config(&config)?;
            let cli = executable(
                &std::env::var("FORKLIFT_SUPABASE_BIN").unwrap_or_else(|_| "supabase".into()),
            )?;
            let pg_bin =
                fs::canonicalize(pg.unwrap_or(Path::new("/opt/homebrew/opt/postgresql@17/bin")))?;
            let b = Binding {
                format: 1,
                root: root.clone(),
                state: state.clone(),
                cli,
                pg_bin,
                config,
            };
            atomic_json(&path, &b)?;
            b
        };
        let mut p = Self {
            binding,
            gate,
            full,
        };
        p.check_binding(&root, &state)?;
        p.settled()?;
        for dir in ["home", "runtime", "captures"] {
            if !state.join(dir).exists() {
                private_dir(&state.join(dir))?;
            }
        }
        p.prepare_runtime()?;
        p.runtime()?.start()?;
        atomic_json(&state.join("ready.json"), &json!({"format":1}))?;
        Ok(p)
    }
    pub fn open(root: &Path, full: bool) -> Result<Self> {
        let (root, state) = Project::state(root, false)?;
        directory(&state)?;
        let gate = Self::lock(&state)?;
        let binding = read(&state.join("supabase.json"))?;
        let p = Self {
            binding,
            gate,
            full,
        };
        p.check_binding(&root, &state)?;
        Ok(p)
    }
    fn check_binding(&self, root: &Path, state: &Path) -> Result<()> {
        if self.binding.format != 1 || self.binding.root != root || self.binding.state != state {
            return fail(
                "project_metadata",
                "Supabase binding belongs to another location",
            );
        }
        validate_config(&self.binding.config)
    }
    fn settled(&self) -> Result<()> {
        self.check_owned()?;
        if self.binding.state.join("pending.json").exists() {
            return fail(
                "project_pending",
                "Supabase restore interrupted; run project recover before commands or edits",
            );
        }
        let config: Config = read(&self.binding.root.join(".forklift.json"))?;
        if config != self.binding.config {
            return fail(
                "project_config",
                "configuration differs from pinned binding; use a fresh worktree for a changed contract",
            );
        }
        Ok(())
    }
    fn prepare_runtime(&mut self) -> Result<()> {
        self.check_owned()?;
        let root = &self.binding.root;
        for name in ["migrations", "seed.sql", "functions"] {
            let relative = Path::new("supabase").join(name);
            if root.join(&relative).exists() && !self.binding.config.source.contains(&relative) {
                return fail(
                    "project_config",
                    format!(
                        "select {} in source so runtime inputs rewind together",
                        relative.display()
                    ),
                );
            }
        }
        let config_path = checked_path(root, Path::new("supabase/config.toml"))?;
        let raw = fs::read_to_string(config_path)?;
        let mut config: toml::Value =
            toml::from_str(&raw).map_err(|_| Error::new("supabase_config", "invalid supabase/config.toml; parser output is suppressed because configuration can contain secrets"))?;
        let table = config
            .as_table_mut()
            .ok_or_else(|| Error::new("supabase_config", "config must be a TOML table"))?;
        // Dedicated local endpoints are allocated by the CLI. Never copy production
        // service keys or dotenv files into this managed runtime.
        for section in [
            "api",
            "db",
            "studio",
            "local_smtp",
            "analytics",
            "edge_runtime",
        ] {
            if let Some(t) = table.get_mut(section).and_then(toml::Value::as_table_mut) {
                for k in [
                    "port",
                    "shadow_port",
                    "smtp_port",
                    "pop3_port",
                    "vector_port",
                    "inspector_port",
                ] {
                    t.remove(k);
                }
                if section == "db"
                    && let Some(t) = t.get_mut("pooler").and_then(toml::Value::as_table_mut)
                {
                    t.remove("port");
                }
            }
        }
        let experimental = table
            .entry("experimental")
            .or_insert(toml::Value::Table(Default::default()))
            .as_table_mut()
            .ok_or_else(|| Error::new("supabase_config", "experimental must be a table"))?;
        experimental.insert("stack".into(), toml::Value::Boolean(true));
        let db = table
            .entry("db")
            .or_insert(toml::Value::Table(Default::default()))
            .as_table_mut()
            .ok_or_else(|| Error::new("supabase_config", "db must be a table"))?;
        let major = db
            .entry("major_version")
            .or_insert(toml::Value::Integer(17));
        if major.as_integer() != Some(17) {
            return fail(
                "supabase_config",
                "this backend supports Supabase PostgreSQL 17; migrate a local seed explicitly before using it",
            );
        }
        let settings = db
            .entry("settings")
            .or_insert(toml::Value::Table(Default::default()))
            .as_table_mut()
            .ok_or_else(|| Error::new("supabase_config", "db.settings must be a table"))?;
        for k in ["fsync", "full_page_writes", "synchronous_commit"] {
            settings.insert(k.into(), toml::Value::String("on".into()));
        }
        let dest = self.binding.state.join("runtime/supabase");
        if !dest.exists() {
            private_dir(&dest)?;
        }
        // Only explicit Supabase project inputs. .temp is runtime-owned and never
        // copied from the user's repository.
        for name in ["migrations", "seed.sql", "functions"] {
            let source = checked_path(root, &Path::new("supabase").join(name))?;
            let target = dest.join(name);
            if target.exists() {
                crate::retention::safe_tree(&target)?;
                if target.is_dir() {
                    fs::remove_dir_all(&target)?;
                } else {
                    fs::remove_file(&target)?;
                }
            }
            if source.exists() {
                copy_item(&source, &target)?;
            }
        }
        let text =
            toml::to_string(&config).map_err(|e| Error::new("supabase_config", e.to_string()))?;
        let cfg = dest.join("config.toml");
        let mut f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(cfg)?;
        use std::io::Write;
        f.write_all(text.as_bytes())?;
        f.sync_all()?;
        sync_dir(&dest)
    }
    pub fn doctor(root: &Path, full: bool) -> Result<Value> {
        let mut checks = Vec::new();
        let result = Self::open(root, full).and_then(|p| {
            p.settled()?;
            let r = p.runtime()?;
            let s = r.status()?;
            if s["readiness"] != "ready" {
                return fail(
                    "supabase_runtime",
                    "local stack is stopped or not ready; run project setup",
                );
            }
            r.verify_durability()?;
            Ok(s)
        });
        match result {Ok(s)=>checks.push(json!({"check":"local_stack","status":"ok","runtime":s.get("runtime"),"readiness":s.get("readiness")})),Err(e)=>checks.push(json!({"check":"local_stack","status":"blocked","message":e.message}))};
        Ok(
            json!({"backend":"supabase","ready":checks.iter().all(|c|c["status"]=="ok"),"checks":checks,"scope":"owned local Supabase native stack; hosted project is unchanged"}),
        )
    }
    pub fn inspect(&self) -> Result<Value> {
        self.settled()?;
        let mut captures = Vec::new();
        if self.binding.state.join("captures").exists() {
            for e in fs::read_dir(self.binding.state.join("captures"))? {
                captures.push(e?.file_name().to_string_lossy().to_string());
            }
        }
        captures.sort();
        Ok(
            json!({"backend":"supabase","runtime":"native","root":self.binding.root,"state":self.binding.state,"captures":captures,"config":self.binding.config}),
        )
    }
    pub fn stop(&self) -> Result<Value> {
        self.runtime()?.stop()
    }
    fn save(&self, path: &Path) -> Result<()> {
        private_dir(path)?;
        let data = path.join("data");
        private_dir(&data)?;
        snapshot(
            &self.binding.root,
            &data.join("source"),
            &self.binding.config,
        )?;
        self.runtime()?.verify_stopped()?;
        let paths = self.runtime()?.snapshot_paths()?;
        for (from, name) in [
            (&paths.stacks_root, "stacks"),
            (&paths.uploads_root, "uploads"),
        ] {
            if from.exists() {
                tree_hash(from)?;
                copy_tree(from, &data.join(name), self.full)?;
            } else {
                private_dir(&data.join(name))?;
            }
        }
        let runtime_source = data.join("runtime_source");
        private_dir(&runtime_source)?;
        for name in ["config.toml", "migrations", "seed.sql", "functions"] {
            let from = self.binding.state.join("runtime/supabase").join(name);
            if from.exists() {
                copy_item(&from, &runtime_source.join(name))?;
            }
        }
        crate::storage::sync_tree(&data)?;
        sync_dir(path)?;
        let saved = Saved {
            format: 1,
            runtime: self.runtime()?.fingerprint()?,
            files: tree_hash(&data)?,
        };
        atomic_json(&path.join("manifest.json"), &saved)?;
        sync_dir(path)
    }
    fn validate_saved(&self, path: &Path) -> Result<()> {
        self.check_owned()?;
        checked_path(
            &self.binding.state,
            path.strip_prefix(&self.binding.state)
                .map_err(|_| Error::new("unsafe_path", "snapshot escapes private state"))?,
        )?;
        let saved: Saved = read(&path.join("manifest.json"))?;
        if saved.format != 1 || saved.runtime != self.runtime()?.fingerprint()? {
            return fail(
                "supabase_compatibility",
                "snapshot runtime differs from installed Supabase runtime",
            );
        }
        if saved.files != tree_hash(&path.join("data"))? {
            return fail("supabase_integrity", "snapshot data checksum mismatch");
        }
        Ok(())
    }
    pub fn capture(&mut self, name: &str) -> Result<Value> {
        self.settled()?;
        validate_name(name)?;
        let target = self.binding.state.join("captures").join(name);
        if target.exists() {
            return fail("exists", "capture already exists");
        }
        self.prepare_runtime()?;
        self.runtime()?.start()?;
        self.runtime()?.stop()?;
        let staging = self.binding.state.join(format!("capture-{}", random_id()?));
        self.save(&staging)?;
        fault("capture_ready");
        crate::storage::publish_new(&staging, &target)?;
        self.runtime()?.start()?;
        Ok(json!({"captured":name,"backend":"supabase"}))
    }
    pub fn restore(&mut self, name: &str) -> Result<Value> {
        self.settled()?;
        validate_name(name)?;
        let saved = self.binding.state.join("captures").join(name);
        self.validate_saved(&saved)?;
        // Preserve dirty source and the previous database/files before publishing intent.
        self.runtime()?.stop()?;
        let retained = self
            .binding
            .state
            .join(format!("retained-{}", random_id()?));
        self.save(&retained)?;
        atomic_json(
            &self.binding.state.join("pending.json"),
            &Journal {
                format: 1,
                name: name.into(),
                starting: false,
            },
        )?;
        fault("restore_journal");
        self.recover()
    }
    fn replace_tree(&self, source: &Path, target: &Path) -> Result<()> {
        self.check_owned()?;
        checked_path(
            &self.binding.state,
            target
                .strip_prefix(&self.binding.state)
                .map_err(|_| Error::new("unsafe_path", "restore target escapes private state"))?,
        )?;
        // Journal remains until all resources and source are restored. Repeating this
        // copy after interruption is safe; the immutable snapshot is never moved.
        if target.exists() {
            tree_hash(target)?;
            fs::remove_dir_all(target)?;
            sync_dir(target.parent().unwrap())?;
        }
        fault("restore_removed");
        copy_tree(source, target, self.full)?;
        sync_dir(target.parent().unwrap())
    }
    pub fn recover(&mut self) -> Result<Value> {
        self.check_owned()?;
        let path = self.binding.state.join("pending.json");
        if !path.exists() {
            return self.inspect();
        }
        let mut j: Journal = read(&path)?;
        if j.format != 1 {
            return fail("project_metadata", "unsupported Supabase recovery journal");
        }
        validate_name(&j.name)?;
        let saved = self.binding.state.join("captures").join(&j.name);
        self.validate_saved(&saved)?;
        if j.starting {
            self.runtime()?.stop()?;
            j.starting = false;
            atomic_json(&path, &j)?;
        }
        let data = saved.join("data");
        let check = self.binding.state.join(format!("check-{}", random_id()?));
        snapshot(&self.binding.root, &check, &self.binding.config)?;
        self.replace_tree(
            &data.join("stacks"),
            &self.binding.state.join("home/stacks"),
        )?;
        let uploads = self
            .binding
            .state
            .join("runtime/supabase/.temp/stack-uploads");
        fs::create_dir_all(uploads.parent().unwrap())?;
        self.replace_tree(&data.join("uploads"), &uploads)?;
        sync_dir(uploads.parent().unwrap())?;
        sync_dir(uploads.parent().unwrap().parent().unwrap())?;
        fault("restore_database");
        for p in &self.binding.config.source {
            let target = checked_path(&self.binding.root, p)?;
            if target.exists() {
                if target.is_dir() {
                    fs::remove_dir_all(&target)?;
                } else {
                    fs::remove_file(&target)?;
                }
                sync_dir(target.parent().unwrap())?;
            }
            let from = data.join("source").join(p);
            if from.exists() {
                fs::create_dir_all(target.parent().unwrap())?;
                copy_item(&from, &target)?;
            }
            let mut ancestor = target.parent();
            while let Some(parent) = ancestor {
                if parent.exists() {
                    sync_dir(parent)?;
                }
                if parent == self.binding.root {
                    break;
                }
                ancestor = parent.parent();
            }
            fault("restore_item");
        }
        // Restore the normalized configuration that actually produced the snapshot.
        let runtime = self.binding.state.join("runtime/supabase");
        for name in ["config.toml", "migrations", "seed.sql", "functions"] {
            let target = runtime.join(name);
            if target.exists() {
                crate::retention::safe_tree(&target)?;
                if target.is_dir() {
                    fs::remove_dir_all(&target)?;
                } else {
                    fs::remove_file(&target)?;
                }
            }
            let from = data.join("runtime_source").join(name);
            if from.exists() {
                copy_item(&from, &target)?;
            }
        }
        sync_dir(&runtime)?;
        let verify = self.binding.state.join(format!("check-{}", random_id()?));
        snapshot(&self.binding.root, &verify, &self.binding.config)?;
        if inventory(&verify)? != inventory(&data.join("source"))? {
            return fail("supabase_integrity", "restored source differs from capture");
        }
        self.runtime()?.verify_stopped()?;
        fault("restore_files");
        j.starting = true;
        atomic_json(&path, &j)?;
        self.runtime()?.start()?;
        fault("restore_started");
        fs::remove_file(&path)?;
        sync_dir(&self.binding.state)?;
        Ok(json!({"restored":j.name,"backend":"supabase"}))
    }
    pub fn forget(&self, name: &str, apply: bool) -> Result<Value> {
        self.settled()?;
        validate_name(name)?;
        let path = self.binding.state.join("captures").join(name);
        directory(&path)?;
        tree_hash(&path)?;
        if apply {
            fs::rename(
                &path,
                self.binding.state.join(format!("capture-{}", random_id()?)),
            )?;
            sync_dir(path.parent().unwrap())?;
            sync_dir(&self.binding.state)?;
        }
        Ok(json!({"applied":apply,"capture":name,"note":"prune reclaims forgotten snapshot files"}))
    }
    pub fn prune(&self, apply: bool) -> Result<Value> {
        self.settled()?;
        let mut paths = Vec::new();
        for e in fs::read_dir(&self.binding.state)? {
            let p = e?.path();
            let n = p.file_name().unwrap().to_string_lossy();
            if ["retained-", "capture-", "check-"].iter().any(|prefix| {
                n.strip_prefix(prefix)
                    .is_some_and(crate::lifecycle::valid_id)
            }) {
                tree_hash(&p)?;
                paths.push(p);
            }
        }
        paths.sort();
        if apply {
            for p in &paths {
                fs::remove_dir_all(p)?;
            }
            sync_dir(&self.binding.state)?;
        }
        Ok(
            json!({"applied":apply,"paths":paths,"note":"current stack, runtime cache and named captures remain retained"}),
        )
    }
    pub fn task(&mut self, name: &str) -> Result<i32> {
        let args = self
            .binding
            .config
            .commands
            .get(name)
            .ok_or_else(|| Error::new("project_config", "unknown configured command"))?
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        self.run(&args)
    }
    pub fn run(&mut self, argv: &[OsString]) -> Result<i32> {
        self.settled()?;
        let (program, args) = argv
            .split_first()
            .ok_or_else(|| Error::new("project_config", "command required"))?;
        self.prepare_runtime()?;
        let status = self.runtime()?.start()?;
        let env = status
            .get("env")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                Error::new(
                    "supabase_runtime",
                    "Supabase status has no connection environment",
                )
            })?;
        let mut cmd = Command::new(program);
        cmd.args(args).current_dir(&self.binding.root);
        for (k, _) in std::env::vars_os() {
            let s = k.to_string_lossy();
            if s.starts_with("PG")
                || s.starts_with("SUPABASE_")
                || s.starts_with("NEXT_PUBLIC_SUPABASE_")
                || s == "DATABASE_URL"
            {
                cmd.env_remove(k);
            }
        }
        for (from, to) in [
            ("API_URL", "SUPABASE_URL"),
            ("ANON_KEY", "SUPABASE_ANON_KEY"),
            ("PUBLISHABLE_KEY", "SUPABASE_PUBLISHABLE_KEY"),
        ] {
            if let Some(v) = env.get(from).and_then(Value::as_str) {
                cmd.env(to, v);
            }
        }
        cmd.env("PG_BIN", &self.binding.pg_bin);
        let mut mapping = BTreeMap::from([
            ("API_URL".to_owned(), "NEXT_PUBLIC_SUPABASE_URL".to_owned()),
            ("ANON_KEY".into(), "NEXT_PUBLIC_SUPABASE_ANON_KEY".into()),
            (
                "PUBLISHABLE_KEY".into(),
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY".into(),
            ),
            (
                "SERVICE_ROLE_KEY".into(),
                "SUPABASE_SERVICE_ROLE_KEY".into(),
            ),
            ("SECRET_KEY".into(), "SUPABASE_SECRET_KEY".into()),
            ("DB_URL".into(), self.binding.config.connection_env.clone()),
        ]);
        mapping.extend(self.binding.config.supabase_env.clone());
        for (from, to) in mapping {
            let v = env.get(&from).and_then(Value::as_str).ok_or_else(|| {
                Error::new(
                    "supabase_runtime",
                    "local runtime omitted a required connection value",
                )
            })?;
            cmd.env(to, v);
        }
        // Retain the cooperative gate in the child, even if the parent is killed.
        let fd = self.gate.as_raw_fd();
        unsafe {
            cmd.pre_exec(move || {
                let flags = libc::fcntl(fd, libc::F_GETFD);
                if flags < 0 || libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let status = cmd.status()?;
        Ok(status
            .code()
            .unwrap_or_else(|| 128 + status.signal().unwrap_or(1)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> Config {
        Config {
            format: 1,
            source: vec!["src".into(), "supabase/config.toml".into()],
            connection_env: "DATABASE_URL".into(),
            backend: Some("supabase".into()),
            supabase_env: BTreeMap::new(),
            database: "postgres".into(),
            tcp: false,
            commands: BTreeMap::new(),
        }
    }
    #[test]
    fn mapping_does_not_allow_server_keys_in_public_clients() {
        for name in [
            "NEXT_PUBLIC_SUPABASE_KEY",
            "VITE_SUPABASE_KEY",
            "PUBLIC_SUPABASE_KEY",
            "PATH",
            "DYLD_INSERT_LIBRARIES",
        ] {
            for source in ["SECRET_KEY", "SERVICE_ROLE_KEY", "DB_URL"] {
                let mut c = config();
                c.supabase_env.insert(source.into(), name.into());
                assert!(validate_config(&c).is_err());
            }
            let mut c = config();
            c.connection_env = name.into();
            assert!(validate_config(&c).is_err());
        }
        let mut c = config();
        c.supabase_env
            .insert("PUBLISHABLE_KEY".into(), "VITE_SUPABASE_KEY".into());
        assert!(validate_config(&c).is_ok());
        c.source = vec!["supabase".into()];
        assert!(validate_config(&c).is_err());
    }
    #[test]
    fn backend_detection_honors_postgres_override_and_existing_binding() -> Result<()> {
        let root = std::env::temp_dir().join(format!("forklift-backend-unit-{}", random_id()?));
        private_dir(&root)?;
        assert!(
            Command::new("git")
                .args(["init", "--quiet"])
                .arg(&root)
                .status()?
                .success()
        );
        assert!(!selected(&root)?);
        private_dir(&root.join("supabase"))?;
        fs::write(
            root.join("supabase/config.toml"),
            "[db]\nmajor_version=17\n",
        )?;
        assert!(selected(&root)?);
        let mut c = config();
        c.backend = Some("postgres".into());
        atomic_json(&root.join(".forklift.json"), &c)?;
        assert!(!selected(&root)?);
        c.backend = None;
        atomic_json(&root.join(".forklift.json"), &c)?;
        assert!(selected(&root)?);
        let (_, state) = Project::state(&root, true)?;
        private_dir(&state)?;
        atomic_json(&state.join("binding.json"), &json!({}))?;
        assert!(!selected(&root)?);
        fs::remove_dir_all(root)?;
        Ok(())
    }
    #[test]
    fn snapshot_hash_detects_content_modes_and_rejects_links() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let dir =
            std::env::temp_dir().join(format!("forklift-supabase-unit-{}", random_id().unwrap()));
        private_dir(&dir).unwrap();
        let p = dir.join("source");
        fs::write(&p, b"before").unwrap();
        let original = tree_hash(&dir).unwrap();
        fs::write(&p, b"after").unwrap();
        assert_ne!(original, tree_hash(&dir).unwrap());
        fs::write(&p, b"before").unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o700)).unwrap();
        assert_ne!(original, tree_hash(&dir).unwrap());
        symlink(&p, dir.join("link")).unwrap();
        assert!(tree_hash(&dir).is_err());
        fs::remove_dir_all(dir).unwrap();
    }
}
