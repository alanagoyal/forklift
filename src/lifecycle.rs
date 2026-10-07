use crate::storage::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io,
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    process::Command,
    time::Instant,
};

#[derive(Debug)]
pub struct Error {
    pub code: &'static str,
    pub message: String,
}
impl Error {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into().chars().take(8000).collect(),
        }
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for Error {}
impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Self::new("io", e.to_string())
    }
}
impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Self::new("catalog", e.to_string())
    }
}
pub type Result<T> = std::result::Result<T, Error>;
pub fn fail<T>(code: &'static str, message: impl Into<String>) -> Result<T> {
    Err(Error::new(code, message))
}
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "snake_case")]
enum State {
    Creating,
    Stopped,
    Starting,
    Running,
    Stopping,
    Failed,
    Ready,
}
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
struct Identity {
    pid: u32,
    pg_start: i64,
    os_start: String,
}
#[derive(Clone, Serialize, Deserialize, Debug)]
struct Instance {
    id: String,
    state: State,
    process: Option<Identity>,
}
impl Instance {
    fn new() -> Result<Self> {
        Ok(Self {
            id: random_id()?,
            state: State::Creating,
            process: None,
        })
    }
}
#[derive(Clone, Serialize, Deserialize)]
struct Branch {
    head: Instance,
    checkpoint: Option<String>,
    pending: Option<Instance>,
    resume: bool,
}
#[derive(Clone, Serialize, Deserialize)]
struct Checkpoint {
    instance: Instance,
    source: String,
    copy_mode: String,
}
#[derive(Serialize, Deserialize)]
struct Catalog {
    format: u32,
    store_id: String,
    pg_bin: PathBuf,
    postgres_major: u32,
    platform: String,
    branches: BTreeMap<String, Branch>,
    checkpoints: BTreeMap<String, Checkpoint>,
}
#[derive(Serialize, Deserialize)]
struct Owner {
    store_id: String,
    generation: String,
}

// Compiled out of ordinary builds. Simulates abrupt CLI death with no cleanup.
fn failpoint(stage: &str) {
    #[cfg(feature = "test-failpoints")]
    if std::env::var("FORKLIFT_FAILPOINT").as_deref() == Ok(stage) {
        std::process::exit(99);
    }
    let _ = stage;
}

pub struct Store {
    root: PathBuf,
    catalog: Catalog,
    _lock: File,
    full: bool,
    events: Vec<Value>,
}
fn command(mut cmd: Command) -> Result<String> {
    for (k, _) in std::env::vars_os() {
        if k.to_string_lossy().starts_with("PG") {
            cmd.env_remove(k);
        }
    }
    cmd.env("PGCONNECT_TIMEOUT", "3").env("LC_ALL", "C");
    let out = cmd.output()?;
    if !out.status.success() {
        return fail(
            "postgres",
            format!(
                "{}: {}",
                cmd.get_program().to_string_lossy(),
                String::from_utf8_lossy(&out.stderr)
            ),
        );
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_owned())
}
fn valid_id(s: &str) -> bool {
    s.len() == 16
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
pub fn validate_name(s: &str) -> Result<()> {
    if s.is_empty()
        || s.len() > 48
        || !s
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
    {
        return fail(
            "invalid_name",
            "names must be 1–48 lowercase letters, digits, hyphens or underscores",
        );
    }
    Ok(())
}
fn read_json<T: serde::de::DeserializeOwned>(p: &Path) -> Result<T> {
    regular(p)?;
    if fs::metadata(p)?.len() > 4 * 1024 * 1024 {
        return fail("catalog", "catalog exceeds 4 MiB limit");
    }
    Ok(serde_json::from_slice(&fs::read(p)?)?)
}
fn quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

impl Store {
    pub fn open(path: &Path, pg_bin: Option<&Path>, create: bool, full: bool) -> Result<Self> {
        let fresh = !path.exists();
        if fresh {
            if !create {
                return fail("not_found", "store does not exist; run init first");
            }
            if let Some(p) = path.parent()
                && !p.as_os_str().is_empty()
            {
                fs::create_dir_all(p)?;
            }
            private_dir(path)?;
        }
        directory(path)?;
        let root = fs::canonicalize(path)?;
        let meta = fs::metadata(&root)?;
        if meta.uid() != unsafe { libc::geteuid() } || meta.permissions().mode() & 0o077 != 0 {
            return fail(
                "unsafe_path",
                "store must be owned by this user with permissions 0700",
            );
        }
        if !fresh {
            regular(&root.join("catalog.json"))?;
        }
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(root.join("lock"))?;
        if lock.metadata()?.nlink() != 1 {
            return fail("unsafe_path", "store lock must not be hard-linked");
        }
        lock.try_lock().map_err(|e| {
            Error::new(
                "locked",
                format!("another command holds this store's lock: {e}"),
            )
        })?;
        let catalog_path = root.join("catalog.json");
        let catalog = if fresh {
            let bin = fs::canonicalize(
                pg_bin.unwrap_or(Path::new("/opt/homebrew/opt/postgresql@17/bin")),
            )?;
            Self::check_version(&bin)?;
            private_dir(&root.join("generations"))?;
            let c = Catalog {
                format: 1,
                store_id: random_id()?,
                pg_bin: bin,
                postgres_major: 17,
                platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
                branches: BTreeMap::new(),
                checkpoints: BTreeMap::new(),
            };
            atomic_json(&catalog_path, &c)?;
            c
        } else {
            read_json(&catalog_path)?
        };
        if catalog.format != 1
            || catalog.postgres_major != 17
            || !valid_id(&catalog.store_id)
            || catalog.platform != format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH)
        {
            return fail(
                "incompatible",
                "unsupported catalog format, platform or PostgreSQL major",
            );
        }
        if let Some(bin) = pg_bin
            && fs::canonicalize(bin)? != catalog.pg_bin
        {
            return fail(
                "incompatible",
                "this store pins another PostgreSQL installation",
            );
        }
        Self::check_version(&catalog.pg_bin)?;
        directory(&root.join("generations"))?;
        let mut generations = BTreeSet::new();
        if catalog.branches.len() + catalog.checkpoints.len() > 1000 {
            return fail("catalog", "too many catalog entries");
        }
        for (name, b) in &catalog.branches {
            if !generations.insert(b.head.id.clone())
                || b.pending
                    .as_ref()
                    .is_some_and(|p| !generations.insert(p.id.clone()))
            {
                return fail("catalog", "duplicate generation identity");
            }
            validate_name(name)?;
            if !valid_id(&b.head.id) || b.pending.as_ref().is_some_and(|p| !valid_id(&p.id)) {
                return fail("catalog", "invalid generation identity");
            }
        }
        for (name, c) in &catalog.checkpoints {
            if !generations.insert(c.instance.id.clone()) {
                return fail("catalog", "duplicate generation identity");
            }
            validate_name(name)?;
            if !valid_id(&c.instance.id) {
                return fail("catalog", "invalid checkpoint identity");
            }
        }
        Ok(Self {
            root,
            catalog,
            _lock: lock,
            full,
            events: vec![],
        })
    }
    fn check_version(bin: &Path) -> Result<()> {
        let mut c = Command::new(bin.join("postgres"));
        c.arg("--version");
        if !command(c)?.starts_with("postgres (PostgreSQL) 17.") {
            return fail("incompatible", "PostgreSQL major 17 is required");
        }
        Ok(())
    }
    fn save(&self) -> Result<()> {
        atomic_json(&self.root.join("catalog.json"), &self.catalog)
    }
    fn gen_dir(&self, i: &Instance) -> PathBuf {
        self.root.join("generations").join(&i.id)
    }
    fn data(&self, i: &Instance) -> PathBuf {
        self.gen_dir(i).join("data")
    }
    fn socket(&self, i: &Instance) -> PathBuf {
        PathBuf::from(format!("/tmp/fl-{}/{}", self.catalog.store_id, i.id))
    }
    fn cmd(&self, tool: &str) -> Command {
        Command::new(self.catalog.pg_bin.join(tool))
    }
    fn record(&mut self, stage: &str, t: Instant) {
        self.events
            .push(json!({"stage":stage,"elapsed_ms":t.elapsed().as_millis()}));
    }
    fn new_generation(&self, i: &Instance) -> Result<()> {
        private_dir(&self.gen_dir(i))?;
        atomic_json(
            &self.gen_dir(i).join("owner.json"),
            &Owner {
                store_id: self.catalog.store_id.clone(),
                generation: i.id.clone(),
            },
        )?;
        sync_dir(&self.root.join("generations"))
    }
    fn owned(&self, i: &Instance) -> Result<()> {
        if !valid_id(&i.id) {
            return fail("unsafe_path", "invalid generation");
        }
        directory(&self.gen_dir(i))?;
        let owner: Owner = read_json(&self.gen_dir(i).join("owner.json"))?;
        if owner.store_id != self.catalog.store_id || owner.generation != i.id {
            return fail("unsafe_path", "generation ownership does not match catalog");
        }
        directory(&self.data(i))?;
        regular(&self.data(i).join("PG_VERSION"))?;
        if fs::read_to_string(self.data(i).join("PG_VERSION"))?.trim() != "17" {
            return fail("incompatible", "generation is not PostgreSQL 17");
        }
        Ok(())
    }
    fn ensure_socket(&self, i: &Instance) -> Result<()> {
        let socket = self.socket(i);
        let parent = socket.parent().unwrap();
        for p in [parent, socket.as_path()] {
            if !p.exists() {
                private_dir(p)?;
            }
            directory(p)?;
            let m = fs::metadata(p)?;
            if m.uid() != unsafe { libc::geteuid() } || m.permissions().mode() & 0o077 != 0 {
                return fail(
                    "unsafe_path",
                    "unsafe socket directory owner or permissions",
                );
            }
        }
        Ok(())
    }
    fn client(&self, i: &Instance, tool: &str) -> Command {
        let mut c = self.cmd(tool);
        c.arg("-h")
            .arg(self.socket(i))
            .args(["-p", "5432", "-U", "forklift", "-d", "postgres"]);
        c
    }
    fn sql(&self, i: &Instance, sql: &str) -> Result<String> {
        let mut c = self.client(i, "psql");
        c.args(["-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql]);
        command(c)
    }
    fn control(&self, i: &Instance) -> Result<String> {
        self.owned(i)?;
        let mut c = self.cmd("pg_controldata");
        c.arg(self.data(i));
        command(c)
    }
    fn clean(&self, i: &Instance) -> Result<()> {
        let c = self.control(i)?;
        if self.data(i).join("postmaster.pid").exists()
            || !c.lines().any(|l| {
                l.starts_with("Database cluster state:") && l.trim_end().ends_with("shut down")
            })
        {
            return fail(
                "not_stopped",
                "physical snapshot requires a verified clean shutdown",
            );
        }
        Ok(())
    }
    // A PID is insufficient: bind it to OS start time, pidfile epoch, SQL data path and socket.
    fn observe(&self, i: &Instance) -> Result<Option<Identity>> {
        self.owned(i)?;
        let pidfile = self.data(i).join("postmaster.pid");
        if !pidfile.exists() {
            return Ok(None);
        }
        regular(&pidfile)?;
        let text = fs::read_to_string(pidfile)?;
        let lines: Vec<_> = text.lines().collect();
        if lines.len() < 5 {
            return fail(
                "process_identity",
                "incomplete postmaster.pid; retry after startup",
            );
        }
        let pid: u32 = lines[0]
            .parse()
            .map_err(|_| Error::new("process_identity", "invalid pid"))?;
        let pg_start: i64 = lines[2]
            .parse()
            .map_err(|_| Error::new("process_identity", "invalid start time"))?;
        if pid < 2
            || Path::new(lines[1]) != self.data(i)
            || lines[3] != "5432"
            || Path::new(lines[4]) != self.socket(i)
        {
            return fail(
                "process_identity",
                "pidfile does not identify this managed instance",
            );
        }
        let mut ps = Command::new("/bin/ps");
        ps.args(["-p", &pid.to_string(), "-o", "lstart=", "-o", "comm="])
            .env("LC_ALL", "C");
        let out = ps.output()?;
        let identity = String::from_utf8_lossy(&out.stdout).trim().to_owned();
        if identity.is_empty() {
            return Ok(None);
        } // Postgres will handle its own stale pidfile on start.
        if !identity.ends_with(
            self.catalog
                .pg_bin
                .join("postgres")
                .to_string_lossy()
                .as_ref(),
        ) {
            return fail(
                "process_identity",
                "PID belongs to another executable; refusing to signal it",
            );
        }
        let current = Identity {
            pid,
            pg_start,
            os_start: identity,
        };
        if i.process.as_ref().is_some_and(|p| p != &current) {
            return fail(
                "process_identity",
                "process identity changed; refusing to adopt or stop it",
            );
        }
        self.ensure_socket(i)?;
        let actual=self.sql(i,"SELECT current_setting('data_directory') || '|' || floor(extract(epoch FROM pg_postmaster_start_time()))::bigint")?;
        let (path, epoch) = actual
            .rsplit_once('|')
            .ok_or_else(|| Error::new("process_identity", "unexpected identity response"))?;
        if Path::new(path) != self.data(i)
            || (epoch.parse::<i64>().unwrap_or(0) - pg_start).abs() > 2
        {
            return fail(
                "process_identity",
                "connected server does not match pidfile identity",
            );
        }
        Ok(Some(current))
    }
    fn persist_instance(&mut self, name: &str, pending: bool, i: &Instance) -> Result<()> {
        let b = self
            .catalog
            .branches
            .get_mut(name)
            .ok_or_else(|| Error::new("not_found", "branch not found"))?;
        if pending {
            b.pending = Some(i.clone());
        } else {
            b.head = i.clone();
        }
        self.save()
    }
    fn initialize(&self, i: &Instance) -> Result<()> {
        self.new_generation(i)?;
        let mut c = self.cmd("initdb");
        c.arg("-D").arg(self.data(i)).args([
            "--no-locale",
            "--encoding=UTF8",
            "--data-checksums",
            "--auth-local=trust",
            "--auth-host=reject",
            "-U",
            "forklift",
        ]);
        command(c)?;
        sync_dir(&self.gen_dir(i))
    }
    fn start_instance(&mut self, name: &str, pending: bool, mut i: Instance) -> Result<Instance> {
        self.owned(&i)?;
        if matches!(i.state, State::Failed | State::Creating) {
            return fail(
                "invalid_state",
                "incomplete or failed generation cannot be started",
            );
        }
        if let Some(p) = self.observe(&i)? {
            i.process = Some(p);
            i.state = State::Running;
            self.persist_instance(name, pending, &i)?;
            return Ok(i);
        }
        i.process = None;
        i.state = State::Starting;
        self.persist_instance(name, pending, &i)?;
        validate_tree(&self.data(&i))?;
        self.ensure_socket(&i)?;
        let args = vec![
            "-p".to_owned(),
            "5432".to_owned(),
            "-k".to_owned(),
            self.socket(&i).to_string_lossy().into_owned(),
            "-c".to_owned(),
            "listen_addresses=".to_owned(),
            "-c".to_owned(),
            format!("data_directory={}", self.data(&i).display()),
            "-c".to_owned(),
            "fsync=on".to_owned(),
            "-c".to_owned(),
            "full_page_writes=on".to_owned(),
            "-c".to_owned(),
            "synchronous_commit=on".to_owned(),
            "-c".to_owned(),
            "shared_buffers=32MB".to_owned(),
            "-c".to_owned(),
            "max_connections=20".to_owned(),
        ];
        let options = args.iter().map(|s| quote(s)).collect::<Vec<_>>().join(" ");
        let t = Instant::now();
        let mut c = self.cmd("pg_ctl");
        c.arg("-D")
            .arg(self.data(&i))
            .arg("-l")
            .arg(self.gen_dir(&i).join("server.log"))
            .args(["-w", "-t", "30", "-o", &options, "start"]);
        command(c)?;
        self.record("postgres_start", t);
        failpoint("start_launched");
        let t = Instant::now();
        i.process = self.observe(&i)?;
        if i.process.is_none() {
            return fail("process_identity", "server disappeared during startup");
        }
        if self.sql(&i,"SELECT current_setting('fsync') || ',' || current_setting('full_page_writes') || ',' || current_setting('synchronous_commit') || ',' || current_setting('data_checksums')")?!="on,on,on,on" {return fail("durability","required PostgreSQL durability settings are not enabled");}
        self.record("first_verified_query", t);
        i.state = State::Running;
        self.persist_instance(name, pending, &i)?;
        Ok(i)
    }
    fn stop_instance(&mut self, name: &str, pending: bool, mut i: Instance) -> Result<Instance> {
        if let Some(p) = self.observe(&i)? {
            i.process = Some(p);
            i.state = State::Stopping;
            self.persist_instance(name, pending, &i)?;
            // Recheck after catalog I/O and immediately before pg_ctl signals the process.
            self.observe(&i)?
                .ok_or_else(|| Error::new("process_identity", "server exited before stop"))?;
            let t = Instant::now();
            let mut c = self.cmd("pg_ctl");
            c.arg("-D")
                .arg(self.data(&i))
                .args(["-w", "-t", "30", "-m", "fast", "stop"]);
            command(c)?;
            self.record("postgres_stop", t);
            self.clean(&i)?;
        }
        i.process = None;
        i.state = State::Stopped;
        self.persist_instance(name, pending, &i)?;
        Ok(i)
    }
    fn get(&self, name: &str) -> Result<Branch> {
        validate_name(name)?;
        self.catalog
            .branches
            .get(name)
            .cloned()
            .ok_or_else(|| Error::new("not_found", format!("branch {name} does not exist")))
    }
    fn ready_checkpoint(&self, name: &str) -> Result<Checkpoint> {
        validate_name(name)?;
        let c = self
            .catalog
            .checkpoints
            .get(name)
            .cloned()
            .ok_or_else(|| Error::new("not_found", "checkpoint not found"))?;
        if c.instance.state != State::Ready {
            return fail("invalid_state", "checkpoint is incomplete or failed");
        }
        self.clean(&c.instance)?;
        Ok(c)
    }
    fn unused(&self, name: &str) -> Result<()> {
        validate_name(name)?;
        if self.catalog.branches.contains_key(name) || self.catalog.checkpoints.contains_key(name) {
            return fail(
                "already_exists",
                "name already exists; nothing was overwritten",
            );
        }
        if self.catalog.branches.len() + self.catalog.checkpoints.len() >= 1000 {
            return fail("limit", "this catalog is limited to 1000 named entries");
        }
        Ok(())
    }
    pub fn init(&mut self, name: &str) -> Result<()> {
        self.unused(name)?;
        let mut i = Instance::new()?;
        self.catalog.branches.insert(
            name.into(),
            Branch {
                head: i.clone(),
                checkpoint: None,
                pending: None,
                resume: false,
            },
        );
        self.save()?;
        self.initialize(&i)?;
        failpoint("init_files_ready");
        self.clean(&i)?;
        i.state = State::Stopped;
        self.persist_instance(name, false, &i)
    }
    pub fn start(&mut self, name: &str) -> Result<()> {
        let b = self.get(name)?;
        self.start_instance(name, false, b.head)?;
        Ok(())
    }
    pub fn stop(&mut self, name: &str) -> Result<()> {
        let b = self.get(name)?;
        if matches!(b.head.state, State::Failed | State::Creating) {
            return fail(
                "invalid_state",
                "failed generation cannot be stopped or promoted to ready",
            );
        }
        self.stop_instance(name, false, b.head)?;
        Ok(())
    }
    fn copy(&mut self, source: &Instance, dest: &Instance) -> Result<()> {
        self.clean(source)?;
        validate_tree(&self.data(source))?;
        self.new_generation(dest)?;
        let t = Instant::now();
        copy_tree(&self.data(source), &self.data(dest), self.full)?;
        sync_dir(&self.gen_dir(dest))?;
        self.clean(dest)?;
        self.record(
            if self.full {
                "full_copy_and_sync"
            } else {
                "apfs_clone_and_sync"
            },
            t,
        );
        Ok(())
    }
    pub fn checkpoint(&mut self, branch: &str, name: &str) -> Result<()> {
        self.unused(name)?;
        let b = self.get(branch)?;
        if matches!(b.head.state, State::Creating | State::Failed) {
            return fail("invalid_state", "cannot snapshot failed branch");
        }
        let mut i = Instance::new()?;
        self.catalog.checkpoints.insert(
            name.into(),
            Checkpoint {
                instance: i.clone(),
                source: branch.into(),
                copy_mode: if self.full { "full" } else { "apfs" }.into(),
            },
        );
        let running = self.observe(&b.head)?.is_some();
        self.catalog.branches.get_mut(branch).unwrap().resume = running;
        self.save()?;
        let interruption = Instant::now();
        let result = (|| {
            let stopped = self.stop_instance(branch, false, b.head)?;
            failpoint("checkpoint_stopped");
            self.copy(&stopped, &i)?;
            i.state = State::Ready;
            self.catalog.checkpoints.get_mut(name).unwrap().instance = i;
            self.save()
        })();
        if result.is_err() {
            self.catalog
                .checkpoints
                .get_mut(name)
                .unwrap()
                .instance
                .state = State::Failed;
            self.save()?;
        }
        if running {
            self.start(branch)?;
            self.record("source_interruption", interruption);
        }
        self.catalog.branches.get_mut(branch).unwrap().resume = false;
        self.save()?;
        result
    }
    pub fn branch(&mut self, source: Option<&str>, from: &str, name: &str) -> Result<()> {
        self.unused(name)?;
        let c = self.ready_checkpoint(from)?;
        if source.is_some_and(|s| s != c.source) {
            return fail(
                "ancestry",
                "checkpoint belongs to a different source branch",
            );
        }
        let mut i = Instance::new()?;
        self.catalog.branches.insert(
            name.into(),
            Branch {
                head: i.clone(),
                checkpoint: Some(from.into()),
                pending: None,
                resume: false,
            },
        );
        self.save()?;
        self.copy(&c.instance, &i)?;
        i.state = State::Stopped;
        self.persist_instance(name, false, &i)
    }
    fn begin_replace(&mut self, name: &str) -> Result<Instance> {
        let b = self.get(name)?;
        if matches!(b.head.state, State::Creating | State::Failed) {
            return fail("invalid_state", "cannot replace a failed branch");
        }
        let i = Instance::new()?;
        let running = self.observe(&b.head)?.is_some();
        let b = self.catalog.branches.get_mut(name).unwrap();
        b.pending = Some(i.clone());
        b.resume = running;
        self.save()?;
        Ok(i)
    }
    fn publish_replace(
        &mut self,
        name: &str,
        i: Instance,
        checkpoint: Option<String>,
    ) -> Result<()> {
        self.clean(&i)?;
        let b = self.get(name)?;
        let interruption = Instant::now();
        self.stop_instance(name, false, b.head)?;
        // The old generation remains intact. One durable catalog rename publishes the new head.
        let b = self.catalog.branches.get_mut(name).unwrap();
        b.head = i;
        b.pending = None;
        b.checkpoint = checkpoint;
        self.save()?;
        failpoint("replacement_published");
        if self.get(name)?.resume {
            self.start(name)?;
        }
        self.catalog.branches.get_mut(name).unwrap().resume = false;
        self.record("replacement_stop_to_ready", interruption);
        self.save()
    }
    pub fn reset(&mut self, name: &str, to: &str) -> Result<()> {
        let c = self.ready_checkpoint(to)?;
        let mut i = self.begin_replace(name)?;
        let result = (|| {
            self.copy(&c.instance, &i)?;
            i.state = State::Stopped;
            self.persist_instance(name, true, &i)?;
            failpoint("replacement_ready");
            self.publish_replace(name, i, Some(to.into()))
        })();
        if result.is_err() {
            self.reconcile()?;
        }
        result
    }
    pub fn import(&mut self, name: &str, dump: &Path) -> Result<()> {
        regular(dump)?;
        let dump = fs::canonicalize(dump)?;
        // Reject SQL scripts and malformed archives before reserving a replacement.
        let mut c = self.cmd("pg_restore");
        c.arg("--list").arg(&dump);
        command(c)?;
        let mut i = self.begin_replace(name)?;
        let result = (|| {
            self.initialize(&i)?;
            i.state = State::Stopped;
            self.persist_instance(name, true, &i)?;
            i = self.start_instance(name, true, i)?;
            let t = Instant::now();
            let mut c = self.client(&i, "pg_restore");
            c.args([
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
            ])
            .arg(&dump);
            command(c)?;
            self.record("logical_import", t);
            i = self.stop_instance(name, true, i)?;
            failpoint("replacement_ready");
            self.publish_replace(name, i, None)
        })();
        if result.is_err() {
            self.reconcile()?;
        }
        result
    }
    pub fn reconcile(&mut self) -> Result<()> {
        // Never publish interrupted creations. Retain their files for inspection.
        for c in self.catalog.checkpoints.values_mut() {
            if c.instance.state == State::Creating {
                c.instance.state = State::Failed;
            }
        }
        let names: Vec<_> = self.catalog.branches.keys().cloned().collect();
        for name in names {
            let b = self.get(&name)?;
            if let Some(mut pending) = b.pending {
                if self.data(&pending).join("PG_VERSION").exists()
                    && let Some(p) = self.observe(&pending)?
                {
                    pending.process = Some(p);
                    self.stop_instance(&name, true, pending)?;
                }
                self.catalog.branches.get_mut(&name).unwrap().pending = None;
            }
            let mut head = self.get(&name)?.head;
            if head.state == State::Creating {
                head.state = State::Failed;
            } else if head.state != State::Failed {
                head.process = self.observe(&head)?;
                head.state = if head.process.is_some() {
                    State::Running
                } else {
                    State::Stopped
                };
            }
            self.catalog.branches.get_mut(&name).unwrap().head = head;
            self.save()?;
            if self.get(&name)?.resume {
                self.start(&name)?;
                self.catalog.branches.get_mut(&name).unwrap().resume = false;
                self.save()?;
            }
        }
        self.save()
    }
    pub fn connection(&self, name: &str) -> Result<Value> {
        let b = self.get(name)?;
        if self.observe(&b.head)?.is_none() {
            return fail("not_running", "branch is stopped; run start first");
        }
        Ok(
            json!({"connection":format!("host={} port=5432 user=forklift dbname=postgres",self.socket(&b.head).display())}),
        )
    }
    pub fn view(&self) -> Result<Value> {
        let branches:Vec<_>=self.catalog.branches.iter().map(|(name,b)|json!({"name":name,"state":b.head.state,"checkpoint":b.checkpoint,"generation":b.head.id,"process":b.head.process})).collect();
        let checkpoints:Vec<_>=self.catalog.checkpoints.iter().map(|(name,c)|json!({"name":name,"state":c.instance.state,"source":c.source,"copy_mode":c.copy_mode})).collect();
        Ok(
            json!({"branches":branches,"checkpoints":checkpoints,"copy_mode":if self.full{"full"}else{"apfs"},"events":self.events}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn names_cannot_escape_store() {
        for n in ["../x", "/tmp/x", "a/b", "a b", "", "UPPER"] {
            assert!(validate_name(n).is_err());
        }
        assert!(validate_name("trial-1_a").is_ok());
    }
    #[test]
    fn shell_arguments_are_quoted() {
        assert_eq!(quote("a'b"), "'a'\\''b'");
    }
}
