//! Stable Git worktree adapter. Only explicitly owned paths are ever replaced.
//! Commands cooperate through the existing paired gate; BB agents must be paused.
use crate::lifecycle::{Error, Result, fail, validate_name};
use crate::objects::digest;
use crate::paired::{PairRef, Workspace, copy_source, inventory, read};
use crate::storage::{atomic_json, directory, private_dir, random_id, sync_dir};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs,
    path::{Component, Path, PathBuf},
    process::Command,
};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub format: u32,
    pub source: Vec<PathBuf>,
    pub connection_env: String,
    pub commands: BTreeMap<String, Vec<String>>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Host {
    store: PathBuf,
    checkpoint: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Binding {
    root: PathBuf,
    config: Config,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RestoreJournal {
    format: u32,
    old: PairRef,
    old_hash: String,
    new_hash: String,
}

fn git(root: &Path, arg: &str) -> Result<PathBuf> {
    let output = Command::new("git")
        .args(["-C"])
        .arg(root)
        .args(["rev-parse", "--path-format=absolute", arg])
        .output()?;
    if !output.status.success() {
        return fail("project_git", "project must be a Git worktree");
    }
    let text = std::str::from_utf8(&output.stdout)
        .map_err(|_| Error::new("project_git", "Git path is not UTF-8"))?;
    Ok(fs::canonicalize(text.trim_end_matches('\n'))?)
}
fn layout(root: &Path) -> Result<(PathBuf, PathBuf, PathBuf)> {
    let root = git(root, "--show-toplevel")?;
    let common = git(&root, "--git-common-dir")?;
    let admin = git(&root, "--absolute-git-dir")?;
    Ok((root, common, admin))
}
fn validate(config: &Config) -> Result<()> {
    if config.format != 1 || config.source.is_empty() || config.source.len() > 128 {
        return fail(
            "project_config",
            "format must be 1, with 1–128 owned source paths",
        );
    }
    for p in &config.source {
        if p.as_os_str().is_empty()
            || p.to_str().is_none()
            || p.components()
                .any(|c| !matches!(c, Component::Normal(s) if s != ".git"))
            || p == Path::new(".forklift.json")
            || p.starts_with(".bb-env-setup.sh")
            || p.starts_with(".bb-env-teardown.sh")
        {
            return fail(
                "project_config",
                "source paths must be relative, exclude Git/config/hooks, and contain no dot components",
            );
        }
        if config.source.iter().any(|q| q != p && p.starts_with(q))
            || config.source.iter().filter(|q| *q == p).count() > 1
        {
            return fail("project_config", "owned source paths must not overlap");
        }
    }
    let key = &config.connection_env;
    if key.is_empty()
        || !key
            .bytes()
            .enumerate()
            .all(|(i, b)| b == b'_' || b.is_ascii_alphabetic() || i > 0 && b.is_ascii_digit())
        || [
            "PATH",
            "HOME",
            "PG_BIN",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
        ]
        .contains(&key.as_str())
    {
        return fail(
            "project_config",
            "connection_env must be a dedicated environment variable name",
        );
    }
    if config
        .commands
        .values()
        .any(|v| v.is_empty() || v[0].is_empty() || v.iter().any(|s| s.contains('\0')))
    {
        return fail(
            "project_config",
            "commands must be nonempty argument arrays",
        );
    }
    Ok(())
}
// Reject links in every ancestor before walking/removing an owned path.
fn checked_path(root: &Path, relative: &Path) -> Result<PathBuf> {
    let mut path = root.to_path_buf();
    for component in relative.components() {
        path.push(component);
        match fs::symlink_metadata(&path) {
            Ok(m) if m.file_type().is_symlink() => {
                return fail("project_source", "owned paths cannot traverse symlinks");
            }
            Ok(_) => (),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(e.into()),
        }
    }
    Ok(path)
}
fn copy_item(source: &Path, dest: &Path) -> Result<()> {
    let m = fs::symlink_metadata(source)?;
    if m.is_dir() {
        copy_source(source, dest)?;
    } else {
        // Reuse the bounded inventory and copy contract for individual files too.
        use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
        if !m.is_file() || m.nlink() != 1 || m.len() > 16 * 1024 * 1024 {
            return fail(
                "project_source",
                "selected file is a link, special file, or exceeds 16 MiB",
            );
        }
        let mut input = fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(source)?;
        let mut out = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(dest)?;
        use std::io::Read;
        if std::io::copy(&mut (&mut input).take(16 * 1024 * 1024 + 1), &mut out)? > 16 * 1024 * 1024
        {
            return fail("project_source", "selected file grew beyond limit");
        }
        out.set_permissions(m.permissions())?;
        out.sync_all()?;
        sync_dir(dest.parent().unwrap())?;
    }
    Ok(())
}
fn snapshot(root: &Path, dest: &Path, config: &Config) -> Result<()> {
    private_dir(dest)?;
    for p in &config.source {
        let source = checked_path(root, p)?;
        if source.try_exists()? {
            let target = dest.join(p);
            fs::create_dir_all(target.parent().unwrap())?;
            copy_item(&source, &target)?;
        }
    }
    inventory(dest)?;
    // Sync synthetic ancestor directories as well as copied owned directories.
    fn sync_tree(p: &Path) -> Result<()> {
        for e in fs::read_dir(p)? {
            let p = e?.path();
            if p.is_dir() {
                sync_tree(&p)?;
            }
        }
        sync_dir(p)
    }
    sync_tree(dest)?;
    sync_dir(dest.parent().unwrap())
}
fn fault(stage: &str) {
    #[cfg(feature = "test-failpoints")]
    if std::env::var("FORKLIFT_PROJECT_TEST_FAILPOINT").as_deref() == Ok(stage) {
        std::process::exit(99);
    }
    let _ = stage;
}

pub struct Project {
    root: PathBuf,
    state: PathBuf,
    config: Config,
    workspace: Workspace,
}
impl Project {
    /// Machine-local seed configuration is shared by this repository's worktrees.
    pub fn configure(root: &Path, store: &Path, checkpoint: &str) -> Result<Value> {
        let (_, common, _) = layout(root)?;
        validate_name(checkpoint)?;
        let host = Host {
            store: fs::canonicalize(store)?,
            checkpoint: checkpoint.into(),
        };
        atomic_json(&common.join("forklift-host.json"), &host)?;
        Ok(json!({"host_config": common.join("forklift-host.json")}))
    }
    fn state(root: &Path, create: bool) -> Result<(PathBuf, PathBuf)> {
        let (root, common, admin) = layout(root)?;
        let id_path = admin.join("forklift-id.json");
        let _claim = if create {
            use std::os::{
                fd::AsRawFd,
                unix::fs::{MetadataExt, OpenOptionsExt},
            };
            let file = fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW)
                .open(admin.join("forklift-setup.lock"))?;
            if !file.metadata()?.is_file() || file.metadata()?.nlink() != 1 {
                return fail("project_metadata", "unsafe setup gate");
            }
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return fail(
                    "project_locked",
                    "another setup is allocating this worktree identity",
                );
            }
            Some(file)
        } else {
            None
        };
        if !id_path.try_exists()? && create {
            atomic_json(&id_path, &random_id()?)?;
        }
        let id: String = read(&id_path)?;
        if id.len() != 16 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
            return fail("project_metadata", "invalid worktree identity");
        }
        let parent = common.join("forklift-workspaces");
        if !parent.try_exists()? && create {
            use std::os::unix::fs::DirBuilderExt;
            match fs::DirBuilder::new().mode(0o700).create(&parent) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(e) => return Err(e.into()),
            }
            sync_dir(&common)?;
        }
        directory(&parent)?;
        let state = parent.join(id);
        if state.starts_with(&root) && !state.starts_with(root.join(".git")) {
            return fail(
                "project_metadata",
                "project state must be outside selected source",
            );
        }
        Ok((root, state))
    }
    pub fn setup(root: &Path, pg: Option<&Path>, full: bool, cache: bool) -> Result<Self> {
        let (root, state) = Self::state(root, true)?;
        if state.try_exists()? {
            return Self::open(&root, pg, full, cache);
        }
        let config: Config = read(&root.join(".forklift.json"))?;
        validate(&config)?;
        let host: Host = read(&git(&root, "--git-common-dir")?.join("forklift-host.json"))?;
        // Publish only a fully initialized binding. Failed attempts retain stopped
        // branches and staging directories; another setup can safely retry.
        let staging = state
            .parent()
            .unwrap()
            .join(format!("setup-{}", random_id()?));
        private_dir(&staging)?;
        sync_dir(staging.parent().unwrap())?;
        atomic_json(
            &staging.join("binding.json"),
            &Binding {
                root: root.clone(),
                config: config.clone(),
            },
        )?;
        let source = staging.join("initial");
        snapshot(&root, &source, &config)?;
        let workspace = Workspace::initialize(
            &staging.join("pair"),
            &source,
            &host.store,
            &host.checkpoint,
            pg,
            full,
            cache,
        )?;
        drop(workspace);
        fault("setup_ready");
        fs::rename(&staging, &state)?;
        sync_dir(state.parent().unwrap())?;
        Self::open(&root, pg, full, cache)
    }
    pub fn open(root: &Path, pg: Option<&Path>, full: bool, cache: bool) -> Result<Self> {
        let (root, state) = Self::state(root, false)?;
        directory(&state)?;
        let binding: Binding = read(&state.join("binding.json"))?;
        validate(&binding.config)?;
        if binding.root != root {
            return fail(
                "project_metadata",
                "worktree moved; binding must not be reused at another path",
            );
        }
        let workspace = Workspace::open(&state.join("pair"), pg, full, cache)?;
        Ok(Self {
            root,
            state,
            config: binding.config,
            workspace,
        })
    }
    fn settled(&self) -> Result<()> {
        if self.state.join("pending.json").try_exists()? {
            return fail(
                "project_pending",
                "worktree restore interrupted; run project recover before commands or edits",
            );
        }
        self.workspace.require_settled()?;
        let config: Config = read(&self.root.join(".forklift.json"))?;
        if config != self.config {
            return fail(
                "project_config",
                "configuration differs from pinned setup; use a fresh worktree for a changed contract",
            );
        }
        Ok(())
    }
    fn mirror(&mut self) -> Result<()> {
        self.settled()?;
        let staging = self.state.join(format!("source-{}", random_id()?));
        snapshot(&self.root, &staging, &self.config)?;
        self.workspace.replace_source(&staging)
    }
    pub fn capture(&mut self, name: &str) -> Result<Value> {
        self.mirror()?;
        self.workspace.capture(name)
    }
    pub fn restore(&mut self, name: &str) -> Result<Value> {
        let new_files = self.workspace.saved_files(name)?;
        self.mirror()?;
        // Current mirror retains all selected pre-rewind dirty/untracked work.
        atomic_json(
            &self.state.join("pending.json"),
            &RestoreJournal {
                format: 1,
                old: self.workspace.current()?,
                old_hash: digest(&serde_json::to_vec(&inventory(
                    &self.workspace.source_path()?,
                )?)?),
                new_hash: digest(&serde_json::to_vec(&new_files)?),
            },
        )?;
        fault("restore_journal");
        self.workspace.restore(name)?;
        fault("restore_pair");
        self.recover()
    }
    pub fn recover(&mut self) -> Result<Value> {
        self.workspace.recover()?;
        let journal = self.state.join("pending.json");
        if journal.try_exists()? {
            let value: RestoreJournal = read(&journal)?;
            if value.format != 1 {
                return fail("project_metadata", "invalid project journal");
            }
            let source = self.workspace.source_path()?;
            let expected_hash = if self.workspace.current()? == value.old {
                value.old_hash
            } else {
                value.new_hash
            };
            let expected = inventory(&source)?;
            if digest(&serde_json::to_vec(&expected)?) != expected_hash {
                return fail(
                    "project_integrity",
                    "selected recovery source checksum mismatch",
                );
            }
            // Preflight *all* owned paths before any deletion. Links/special files refuse.
            let check = self.state.join(format!("preflight-{}", random_id()?));
            snapshot(&self.root, &check, &self.config)?;
            for p in &self.config.source {
                let target = checked_path(&self.root, p)?;
                if target.try_exists()? {
                    if target.is_dir() {
                        fs::remove_dir_all(&target)?;
                    } else {
                        fs::remove_file(&target)?;
                    }
                    sync_dir(target.parent().unwrap())?;
                }
                fault("restore_removed");
                let saved = source.join(p);
                if saved.try_exists()? {
                    fs::create_dir_all(target.parent().unwrap())?;
                    copy_item(&saved, &target)?;
                }
                let mut ancestor = target.parent();
                while let Some(p) = ancestor {
                    if p.exists() {
                        sync_dir(p)?;
                    }
                    if p == self.root {
                        break;
                    }
                    ancestor = p.parent();
                }
                fault("restore_item");
            }
            let verify = self.state.join(format!("verify-{}", random_id()?));
            snapshot(&self.root, &verify, &self.config)?;
            if inventory(&verify)? != expected {
                return fail(
                    "project_integrity",
                    "restored source differs from selected pair",
                );
            }
            fault("restore_files");
            fs::remove_file(journal)?;
            sync_dir(&self.state)?;
        }
        self.view()
    }
    pub fn inspect(&self) -> Result<Value> {
        self.settled()?;
        self.view()
    }
    fn view(&self) -> Result<Value> {
        Ok(
            json!({"root":self.root,"state":self.state,"config":self.config,"pair":self.workspace.inspect()?}),
        )
    }
    pub fn stop(&mut self) -> Result<Value> {
        if self.state.join("pending.json").try_exists()? {
            return fail(
                "project_pending",
                "run project recover before stopping an interrupted restore",
            );
        }
        // Parking a database must remain possible even if app configuration changed.
        self.workspace.stop()
    }
    pub fn run(&mut self, argv: &[OsString]) -> Result<i32> {
        self.settled()?;
        self.workspace
            .run_at(argv, &self.root, &self.config.connection_env, true)
    }
    pub fn task(&mut self, name: &str) -> Result<i32> {
        let argv = self
            .config
            .commands
            .get(name)
            .ok_or_else(|| Error::new("project_command", "command is not configured"))?
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        self.run(&argv)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn selection_is_explicit_and_nonoverlapping() {
        let mut c = Config {
            format: 1,
            source: vec!["src".into(), "package.json".into()],
            connection_env: "DATABASE_URL".into(),
            commands: BTreeMap::new(),
        };
        assert!(validate(&c).is_ok());
        for paths in [
            vec!["."],
            vec!["../outside"],
            vec!["/abs"],
            vec![".git/config"],
            vec!["src", "src/app"],
            vec!["src", "src"],
            vec![".forklift.json"],
        ] {
            c.source = paths.into_iter().map(PathBuf::from).collect();
            assert!(validate(&c).is_err());
        }
    }
}
