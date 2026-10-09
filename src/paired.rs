//! Cooperative application checkpoints. The pair pointer, not two independent
//! restores, is the publication boundary. External writers are not coordinated.
use crate::lifecycle::{Error, Result, Store, fail, validate_name};
use crate::objects::digest;
use crate::storage::{atomic_json, directory, private_dir, random_id, sync_dir};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::Read,
    os::{
        fd::AsRawFd,
        unix::{
            fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
            process::{CommandExt, ExitStatusExt},
        },
    },
    path::{Component, Path, PathBuf},
    process::Command,
};

const MAX_ENTRIES: usize = 10_000;
const MAX_FILE: u64 = 16 * 1024 * 1024;
const MAX_METADATA: u64 = 16 * 1024 * 1024;

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(untagged)]
pub enum Entry {
    File { sha256: String, mode: u32 },
    Directory { directory: bool },
}
pub type Inventory = BTreeMap<String, Entry>;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    format: u32,
    store: PathBuf,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PairRef {
    code: String,
    branch: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SavedPair {
    format: u32,
    code: String,
    files: Inventory,
    checkpoint: String,
    database_receipt: Value,
}
#[derive(Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
enum Journal {
    Capture { checkpoint: String },
    Restore { old: PairRef, new: PairRef },
}

fn read<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?;
    let m = file.metadata()?;
    if !m.is_file() || m.nlink() != 1 || m.len() > MAX_METADATA {
        return fail("pair_metadata", "invalid or oversized workspace metadata");
    }
    let mut bytes = Vec::new();
    (&mut file).take(MAX_METADATA + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_METADATA {
        return fail("pair_metadata", "metadata exceeds limit");
    }
    Ok(serde_json::from_slice(&bytes)?)
}
fn pair_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 40
        || !name.is_ascii()
        || !name
            .bytes()
            .enumerate()
            .all(|(i, b)| b == b'_' || b.is_ascii_alphabetic() || i > 0 && b.is_ascii_digit())
    {
        return fail(
            "pair_name",
            "pair name must be an ASCII identifier, at most 40 characters",
        );
    }
    Ok(())
}
fn relative_code(root: &Path, code: &str, prefix: &str) -> Result<PathBuf> {
    let parts: Vec<_> = Path::new(code).components().collect();
    if parts.len() != 2
        || parts[0] != Component::Normal(prefix.as_ref())
        || !matches!(parts[1], Component::Normal(_))
    {
        return fail("pair_metadata", "invalid source generation path");
    }
    directory(&root.join(prefix))?;
    let path = root.join(code);
    directory(&path)?;
    Ok(path)
}
fn validate_ref(root: &Path, pair: &PairRef) -> Result<()> {
    relative_code(root, &pair.code, "code")?;
    validate_name(&pair.branch)
}
fn pair_fault(stage: &str) {
    #[cfg(feature = "test-failpoints")]
    if std::env::var("FORKLIFT_PAIR_TEST_FAILPOINT").as_deref() == Ok(stage) {
        std::process::exit(99);
    }
    let _ = stage;
}

/// Hash a bounded source tree independently of Git. No links or special files.
pub fn inventory(root: &Path) -> Result<Inventory> {
    directory(root)?;
    let mut result = Inventory::new();
    fn walk(root: &Path, path: &Path, result: &mut Inventory, depth: usize) -> Result<()> {
        if depth > 64 {
            return fail("pair_source", "source directory depth exceeds 64");
        }
        for entry in fs::read_dir(path)? {
            let p = entry?.path();
            if result.len() >= MAX_ENTRIES {
                return fail("pair_source", "source exceeds 10000 entries");
            }
            if p.file_name().is_some_and(|s| s == ".git") {
                return fail("pair_source", "source must exclude .git metadata");
            }
            let key = p
                .strip_prefix(root)
                .unwrap()
                .to_str()
                .ok_or_else(|| Error::new("pair_source", "source paths must be UTF-8"))?
                .to_owned();
            let meta = fs::symlink_metadata(&p)?;
            if meta.is_dir() {
                result.insert(key, Entry::Directory { directory: true });
                walk(root, &p, result, depth + 1)?;
            } else if meta.is_file() && meta.nlink() == 1 && meta.len() <= MAX_FILE {
                let file = OpenOptions::new()
                    .read(true)
                    .custom_flags(libc::O_NOFOLLOW)
                    .open(&p)?;
                let mut bytes = Vec::new();
                file.take(MAX_FILE + 1).read_to_end(&mut bytes)?;
                if bytes.len() as u64 > MAX_FILE {
                    return fail("pair_source", "source file exceeds 16 MiB");
                }
                result.insert(
                    key,
                    Entry::File {
                        sha256: digest(&bytes),
                        mode: meta.mode() & 0o7777,
                    },
                );
            } else {
                return fail(
                    "pair_source",
                    "source contains a link, special file, or file exceeding 16 MiB",
                );
            }
        }
        Ok(())
    }
    walk(root, root, &mut result, 0)?;
    Ok(result)
}
fn copy_source(source: &Path, dest: &Path) -> Result<Inventory> {
    let before = inventory(source)?;
    private_dir(dest)?;
    for (name, entry) in &before {
        let target = dest.join(name);
        match entry {
            Entry::Directory { .. } => private_dir(&target)?,
            Entry::File { mode, .. } => {
                let mut input = OpenOptions::new()
                    .read(true)
                    .custom_flags(libc::O_NOFOLLOW)
                    .open(source.join(name))?;
                let mut output = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&target)?;
                let copied = std::io::copy(&mut (&mut input).take(MAX_FILE + 1), &mut output)?;
                if copied > MAX_FILE {
                    return fail("pair_source", "source file grew beyond limit");
                }
                output.set_permissions(fs::Permissions::from_mode(*mode))?;
                output.sync_all()?;
            }
        }
    }
    // Apply directory modes after filling them. Empty directories are preserved.
    for (name, entry) in before.iter().rev() {
        if matches!(entry, Entry::Directory { .. }) {
            let p = dest.join(name);
            fs::set_permissions(&p, fs::metadata(source.join(name))?.permissions())?;
            sync_dir(&p)?;
        }
    }
    fs::set_permissions(dest, fs::metadata(source)?.permissions())?;
    sync_dir(dest)?;
    sync_dir(dest.parent().unwrap())?;
    if inventory(dest)? != before || inventory(source)? != before {
        return fail("pair_source", "source changed while paused");
    }
    Ok(before)
}

pub struct Workspace {
    root: PathBuf,
    config: Config,
    gate: File,
    pg_bin: Option<PathBuf>,
    full: bool,
    cache: bool,
}
impl Workspace {
    pub fn open(root: &Path, pg_bin: Option<&Path>, full: bool, cache: bool) -> Result<Self> {
        directory(root)?;
        let root = fs::canonicalize(root)?;
        let gate = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(root.join("gate.lock"))?;
        if !gate.metadata()?.is_file() || gate.metadata()?.nlink() != 1 {
            return fail("pair_metadata", "unsafe gate file");
        }
        // flock matches the original Python gate, including across an exec'd child.
        if unsafe { libc::flock(gate.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return fail(
                "pair_locked",
                "another command or inherited child holds this workspace gate",
            );
        }
        let config: Config = read(&root.join("workspace.json"))?;
        if config.format != 1 || !config.store.is_absolute() {
            return fail(
                "pair_metadata",
                "unsupported workspace format or store path",
            );
        }
        for name in ["code", "saved", "pairs"] {
            directory(&root.join(name))?;
        }
        Ok(Self {
            root,
            config,
            gate,
            pg_bin: pg_bin.map(Path::to_path_buf),
            full,
            cache,
        })
    }
    pub fn initialize(
        root: &Path,
        source: &Path,
        store: &Path,
        checkpoint: &str,
        pg_bin: Option<&Path>,
        full: bool,
        cache: bool,
    ) -> Result<Self> {
        let source = fs::canonicalize(source)?;
        let store = fs::canonicalize(store)?;
        if root.exists() {
            return fail("pair_exists", "workspace destination must be new");
        }
        let parent = root
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        fs::create_dir_all(parent)?;
        let root = fs::canonicalize(parent)?.join(
            root.file_name()
                .ok_or_else(|| Error::new("pair_path", "invalid workspace path"))?,
        );
        if root.starts_with(&source) || root.starts_with(&store) {
            return fail(
                "pair_path",
                "workspace must be outside the selected source and database store",
            );
        }
        inventory(&source)?;
        private_dir(&root)?;
        sync_dir(root.parent().unwrap())?;
        for name in ["code", "saved", "pairs"] {
            private_dir(&root.join(name))?;
        }
        sync_dir(&root)?;
        atomic_json(&root.join("workspace.json"), &Config { format: 1, store })?;
        let mut w = Self::open(&root, pg_bin, full, cache)?;
        copy_source(&source, &root.join("code/initial"))?;
        let branch = format!("app-{}", random_id()?);
        w.database(|s| s.branch(None, checkpoint, &branch))?;
        atomic_json(
            &root.join("current.json"),
            &PairRef {
                code: "code/initial".into(),
                branch,
            },
        )?;
        Ok(w)
    }
    fn database<T>(&mut self, action: impl FnOnce(&mut Store) -> Result<T>) -> Result<T> {
        let mut store = Store::open(&self.config.store, self.pg_bin.as_deref(), false, self.full)?;
        store.set_cache_enabled(self.cache);
        store.reconcile()?;
        action(&mut store)
    }
    fn current(&self) -> Result<PairRef> {
        let pair = read(&self.root.join("current.json"))?;
        validate_ref(&self.root, &pair)?;
        Ok(pair)
    }
    fn pending(&self) -> PathBuf {
        self.root.join("pending.json")
    }
    pub fn require_settled(&self) -> Result<()> {
        if self.pending().try_exists()? {
            return fail(
                "pair_pending",
                "workspace paused after interruption; run pair recover",
            );
        }
        Ok(())
    }
    fn finish(&self) -> Result<()> {
        fs::remove_file(self.pending())?;
        sync_dir(&self.root)
    }
    pub fn recover(&mut self) -> Result<Value> {
        self.database(|_| Ok(()))?;
        if self.pending().try_exists()? {
            let journal: Journal = read(&self.pending())?;
            if let Journal::Restore { old, new } = journal {
                let current = self.current()?;
                if current != old && current != new {
                    return fail(
                        "pair_metadata",
                        "current reference matches neither journal generation",
                    );
                }
                let discard = if current == new {
                    old.branch
                } else {
                    new.branch
                };
                validate_name(&discard)?;
                self.database(|s| {
                    let view = s.view()?;
                    if view["branches"].as_array().is_some_and(|bs| {
                        bs.iter()
                            .any(|b| b["name"] == discard && b["state"] != "failed")
                    }) {
                        s.stop(&discard)?;
                    }
                    Ok(())
                })?;
            }
            self.finish()?;
        }
        Ok(json!({"current": self.current()?}))
    }
    pub fn capture(&mut self, name: &str) -> Result<Value> {
        self.require_settled()?;
        pair_name(name)?;
        let path = self.root.join("pairs").join(format!("{name}.json"));
        if path.try_exists()? {
            return fail("pair_exists", "pair already exists");
        }
        let current = self.current()?;
        let token = random_id()?;
        let checkpoint = format!("pair-{token}");
        atomic_json(
            &self.pending(),
            &Journal::Capture {
                checkpoint: checkpoint.clone(),
            },
        )?;
        let files = copy_source(
            &self.root.join(&current.code),
            &self.root.join("saved").join(&token),
        )?;
        pair_fault("capture_code");
        let receipt = self.database(|s| {
            s.checkpoint(&current.branch, &checkpoint)?;
            s.view()
        })?;
        pair_fault("capture_database");
        let pair = SavedPair {
            format: 1,
            code: format!("saved/{token}"),
            files,
            checkpoint,
            database_receipt: receipt,
        };
        atomic_json(&path, &pair)?;
        pair_fault("capture_published");
        self.finish()?;
        Ok(serde_json::to_value(pair)?)
    }
    pub fn restore(&mut self, name: &str) -> Result<Value> {
        self.require_settled()?;
        pair_name(name)?;
        let pair: SavedPair = read(&self.root.join("pairs").join(format!("{name}.json")))?;
        if pair.format != 1 {
            return fail("pair_metadata", "unsupported saved pair format");
        }
        validate_name(&pair.checkpoint)?;
        let source = relative_code(&self.root, &pair.code, "saved")?;
        if inventory(&source)? != pair.files {
            return fail("pair_integrity", "saved source checksum mismatch");
        }
        let token = random_id()?;
        let new = PairRef {
            code: format!("code/{token}"),
            branch: format!("rewind-{token}"),
        };
        atomic_json(
            &self.pending(),
            &Journal::Restore {
                old: self.current()?,
                new: new.clone(),
            },
        )?;
        copy_source(&source, &self.root.join(&new.code))?;
        pair_fault("restore_code");
        self.database(|s| s.branch(None, &pair.checkpoint, &new.branch))?;
        pair_fault("restore_database");
        self.database(|s| s.start(&new.branch))?;
        pair_fault("restore_started");
        atomic_json(&self.root.join("current.json"), &new)?;
        pair_fault("restore_published");
        self.recover()?;
        Ok(serde_json::to_value(new)?)
    }
    pub fn inspect(&self) -> Result<Value> {
        self.require_settled()?;
        let current = self.current()?;
        let mut paths = fs::read_dir(self.root.join("pairs"))?
            .map(|e| e.map(|e| e.path()))
            .collect::<std::io::Result<Vec<_>>>()?;
        paths.sort();
        let pairs = paths
            .iter()
            .filter(|p| p.extension().is_some_and(|e| e == "json"))
            .map(|p| read::<SavedPair>(p))
            .collect::<Result<Vec<_>>>()?;
        Ok(
            json!({"files": inventory(&self.root.join(&current.code))?, "current":current, "pairs":pairs}),
        )
    }
    pub fn stop(&mut self) -> Result<Value> {
        self.require_settled()?;
        let current = self.current()?;
        self.database(|s| {
            s.stop(&current.branch)?;
            s.view()
        })
    }
    /// The child inherits only the cooperative gate, not the database store lock.
    pub fn run(&mut self, argv: &[OsString]) -> Result<i32> {
        self.require_settled()?;
        let (program, args) = argv
            .split_first()
            .ok_or_else(|| Error::new("usage", "pair run needs a command after --"))?;
        let current = self.current()?;
        let (url, bin) = self.database(|s| {
            s.start(&current.branch)?;
            Ok((s.connection(&current.branch)?, s.postgres_bin().to_owned()))
        })?;
        let fd = self.gate.as_raw_fd();
        let mut command = Command::new(program);
        command
            .args(args)
            .current_dir(self.root.join(&current.code))
            .env(
                "DATABASE_URL",
                url["connection"]
                    .as_str()
                    .ok_or_else(|| Error::new("pair_connection", "missing database connection"))?,
            )
            .env("PG_BIN", bin)
            .env("PYTHONDONTWRITEBYTECODE", "1");
        // fcntl is async-signal-safe. Clearing CLOEXEC in the child preserves the
        // parent's normal descriptor policy and keeps the lock alive on exec.
        unsafe {
            command.pre_exec(move || {
                let flags = libc::fcntl(fd, libc::F_GETFD);
                if flags < 0 || libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let status = command.status()?;
        Ok(status
            .code()
            .unwrap_or_else(|| 128 + status.signal().unwrap_or(1)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn names_and_references_are_bounded() {
        for name in ["", "../pair", "bad-name", "1pair", "é", &"x".repeat(41)] {
            assert!(pair_name(name).is_err(), "accepted {name}");
        }
        for name in ["baseline", "_saved", "Version2"] {
            assert!(pair_name(name).is_ok());
        }
        for path in [
            "../escape",
            "code/../escape",
            "/code/escape",
            "saved/id",
            "code/a/b",
        ] {
            assert!(relative_code(Path::new("/nonexistent"), path, "code").is_err());
        }
    }

    #[test]
    fn copies_hidden_files_empty_directories_and_modes_rejects_links() -> Result<()> {
        let root = std::env::temp_dir().join(format!("forklift-pair-test-{}", random_id()?));
        private_dir(&root)?;
        let source = root.join("source");
        private_dir(&source)?;
        private_dir(&source.join("empty"))?;
        fs::write(source.join(".ignored"), b"local dirty bytes\n")?;
        fs::set_permissions(source.join(".ignored"), fs::Permissions::from_mode(0o751))?;
        fs::set_permissions(source.join("empty"), fs::Permissions::from_mode(0o750))?;
        let inv = copy_source(&source, &root.join("dest"))?;
        assert_eq!(inv, inventory(&root.join("dest"))?);
        assert_eq!(fs::metadata(root.join("dest/empty"))?.mode() & 0o777, 0o750);
        assert_eq!(
            fs::metadata(root.join("dest/.ignored"))?.mode() & 0o777,
            0o751
        );
        symlink(source.join(".ignored"), source.join("link"))?;
        assert!(inventory(&source).is_err());
        fs::remove_file(source.join("link"))?;
        fs::hard_link(source.join(".ignored"), source.join("hard"))?;
        assert!(inventory(&source).is_err());
        fs::remove_file(source.join("hard"))?;
        private_dir(&source.join(".git"))?;
        assert!(inventory(&source).is_err());
        fs::remove_dir_all(root)?;
        Ok(())
    }
}
