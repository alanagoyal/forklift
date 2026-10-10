use crate::objects::{self, Compatibility, Extension, Repository};
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
    #[serde(default)]
    snapshot_stop: bool,
    #[serde(default)]
    origin_manifest: Option<String>,
}
#[derive(Clone, Serialize, Deserialize)]
struct Checkpoint {
    instance: Instance,
    source: String,
    copy_mode: String,
    #[serde(default)]
    manifest: Option<String>,
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
    #[serde(default = "default_running_limit")]
    max_running: usize,
}
#[derive(Serialize, Deserialize)]
struct Owner {
    store_id: String,
    generation: String,
}

// Compiled out of ordinary builds. Simulates abrupt CLI death with no cleanup.
pub(crate) fn failpoint(stage: &str) {
    #[cfg(feature = "test-failpoints")]
    if std::env::var("FORKLIFT_FAILPOINT").as_deref() == Ok(stage) {
        std::process::exit(99);
    }
    let _ = stage;
}

fn default_running_limit() -> usize {
    3
}

pub struct Store {
    root: PathBuf,
    catalog: Catalog,
    _lock: File,
    full: bool,
    use_cache: bool,
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
    pub fn postgres_bin(&self) -> &Path {
        &self.catalog.pg_bin
    }

    pub fn open(path: &Path, pg_bin: Option<&Path>, create: bool, full: bool) -> Result<Self> {
        let fresh = !path.exists();
        // Check prerequisites before creating anything. A typo or missing install
        // must not leave a directory that prevents retrying the same command.
        let initial_bin = if fresh && create {
            let requested = pg_bin.unwrap_or(Path::new("/opt/homebrew/opt/postgresql@17/bin"));
            let resolve = || -> Result<PathBuf> {
                let bin = fs::canonicalize(requested)?;
                Self::check_version(&bin)?;
                Ok(bin)
            };
            Some(resolve().map_err(|e| {
                Error::new(
                    "postgres_installation",
                    format!(
                        "cannot use PostgreSQL 17 at {}: {e}; install PostgreSQL 17 or set --pg-bin to its bin directory, then retry",
                        requested.display()
                    ),
                )
            })?)
        } else {
            None
        };
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
            if !root.join("catalog.json").try_exists()? {
                return fail(
                    "not_store",
                    "directory has no Forklift catalog; choose a new --home directory (existing directories are never adopted)",
                );
            }
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
        let mut catalog: Catalog = if fresh {
            let bin = initial_bin.expect("new stores validate PostgreSQL before creation");
            private_dir(&root.join("generations"))?;
            let c = Catalog {
                format: 3,
                store_id: random_id()?,
                pg_bin: bin,
                postgres_major: 17,
                platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
                branches: BTreeMap::new(),
                checkpoints: BTreeMap::new(),
                max_running: default_running_limit(),
            };
            atomic_json(&catalog_path, &c)?;
            c
        } else {
            read_json(&catalog_path)?
        };
        if ![1, 2, 3].contains(&catalog.format)
            || !(1..=100).contains(&catalog.max_running)
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
        if catalog.format < 3 {
            catalog.format = 3;
            atomic_json(&catalog_path, &catalog)?;
        }
        Ok(Self {
            root,
            catalog,
            _lock: lock,
            full,
            use_cache: true,
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
        self.require_running_slot()?;
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
        if let Some(id) = &c.manifest {
            self.repository()?.load(id, &self.compatibility()?)?;
        } else {
            self.clean(&c.instance)?;
        }
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
                snapshot_stop: false,
                origin_manifest: None,
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
        // An explicit stop also cancels a capacity-deferred recovery restart.
        self.catalog.branches.get_mut(name).unwrap().resume = false;
        self.save()?;
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
    fn repository(&self) -> Result<Repository> {
        Repository::open(&self.root.join("objects"))
    }
    fn compatibility(&self) -> Result<Compatibility> {
        let mut version = self.cmd("postgres");
        version.arg("--version");
        let mut build = self.cmd("pg_config");
        build.arg("--configure");
        let mut sharedir = self.cmd("pg_config");
        sharedir.arg("--sharedir");
        let mut libdir = self.cmd("pg_config");
        libdir.arg("--pkglibdir");
        Ok(Compatibility {
            postgres_major: 17,
            postgres_version: command(version)?,
            platform: self.catalog.platform.clone(),
            build_configuration: command(build)?,
            extension_installations: objects::extension_installations(
                Path::new(&command(sharedir)?),
                Path::new(&command(libdir)?),
            ),
        })
    }
    fn extensions(&self, i: &Instance) -> Result<Vec<Extension>> {
        if self.sql(i,"SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')")? != "0" ||
           !self.sql(i,"SHOW shared_preload_libraries")?.is_empty() ||
           self.sql(i,"SELECT count(*) FROM pg_database WHERE NOT datallowconn AND datname <> 'template0'")? != "0" {
            return fail("incompatible","custom tablespaces, preloaded libraries and nonconnectable user databases are unsupported");
        }
        let names: Vec<String> = serde_json::from_str(&self.sql(
            i,
            "SELECT json_agg(datname ORDER BY datname) FROM pg_database WHERE datallowconn",
        )?)?;
        let compatibility = self.compatibility()?;
        let mut extensions = Vec::new();
        for db in names {
            let mut c = self.client(i, "psql");
            c.args(["-d",&db,"-XAt","-v","ON_ERROR_STOP=1","-c","SELECT coalesce(json_agg(json_build_object('database',current_database(),'name',extname,'version',extversion) ORDER BY extname), '[]') FROM pg_extension"]);
            let mut found: Vec<Extension> = serde_json::from_str(&command(c)?)?;
            for e in &mut found {
                e.installation_sha256 = compatibility.extension_installations.get(&e.name).cloned();
            }
            // The catalog version must still be offered by the local installation.
            let mut available = self.client(i, "psql");
            available.args(["-d", &db, "-XAt", "-v", "ON_ERROR_STOP=1", "-c",
                "SELECT count(*) FROM pg_extension e WHERE NOT EXISTS (SELECT 1 FROM pg_available_extension_versions a WHERE a.name=e.extname AND a.version=e.extversion)"]);
            if command(available)? != "0" {
                return fail(
                    "incompatible",
                    "installed extension version is unavailable locally",
                );
            }
            let mut libraries = self.client(i, "psql");
            libraries.args(["-d", &db, "-XAt", "-v", "ON_ERROR_STOP=1", "-c",
                "SELECT count(*) FROM pg_extension e JOIN pg_depend d ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid AND d.classid='pg_proc'::regclass AND d.deptype='e' JOIN pg_proc p ON p.oid=d.objid JOIN pg_language l ON l.oid=p.prolang WHERE l.lanname='c' AND p.probin IS DISTINCT FROM '$libdir/' || e.extname"]);
            if command(libraries)? != "0" {
                return fail(
                    "incompatible",
                    "extension contains nonstandard C function libraries",
                );
            }
            if !found.is_empty() {
                objects::validate_extensions(&found, &compatibility)?;
            }
            extensions.extend(found);
        }
        objects::validate_extensions(&extensions, &compatibility)?;
        Ok(extensions)
    }
    pub fn checkpoint(&mut self, branch: &str, name: &str) -> Result<()> {
        self.unused(name)?;
        let b = self.get(branch)?;
        if matches!(b.head.state, State::Creating | State::Failed) {
            return fail("invalid_state", "cannot snapshot failed branch");
        }
        let running = self.observe(&b.head)?.is_some();
        self.catalog.checkpoints.insert(
            name.into(),
            Checkpoint {
                instance: Instance::new()?,
                source: branch.into(),
                copy_mode: "object-packs".into(),
                manifest: None,
            },
        );
        let b = self.catalog.branches.get_mut(branch).unwrap();
        b.resume = running;
        b.snapshot_stop = !running;
        self.save()?;
        let mut interruption = None;
        let result = (|| {
            self.start(branch)?;
            let i = self.get(branch)?.head;
            let extensions = self.extensions(&i)?;
            let compatibility = self.compatibility()?;
            interruption = Some(Instant::now());
            let stopped = self.stop_instance(branch, false, i)?;
            failpoint("checkpoint_stopped");
            self.clean(&stopped)?;
            let (id, stats) =
                self.repository()?
                    .capture(&self.data(&stopped), compatibility, extensions)?;
            self.events
                .push(json!({"stage":"object_capture","metrics":stats,"manifest":id}));
            let c = self.catalog.checkpoints.get_mut(name).unwrap();
            c.instance.state = State::Ready;
            c.manifest = Some(id);
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
            if let Some(t) = interruption {
                self.record("source_interruption", t);
            }
        } else {
            self.stop(branch)?;
        }
        let b = self.catalog.branches.get_mut(branch).unwrap();
        b.resume = false;
        b.snapshot_stop = false;
        self.save()?;
        result
    }
    pub fn set_cache_enabled(&mut self, enabled: bool) {
        self.use_cache = enabled;
    }
    fn active_count(&self) -> Result<usize> {
        let mut count = 0;
        for b in self.catalog.branches.values() {
            for i in std::iter::once(&b.head).chain(b.pending.iter()) {
                if self.data(i).join("postmaster.pid").exists() && self.observe(i)?.is_some() {
                    count += 1;
                }
            }
        }
        Ok(count)
    }
    pub(crate) fn require_running_slot(&self) -> Result<()> {
        if self.active_count()? >= self.catalog.max_running {
            return fail(
                "running_limit",
                format!(
                    "running-cluster limit {} reached; stop a branch or change config --max-running",
                    self.catalog.max_running
                ),
            );
        }
        Ok(())
    }
    pub fn config(&mut self, max_running: Option<usize>) -> Result<()> {
        if let Some(limit) = max_running {
            if !(1..=100).contains(&limit) {
                return fail("invalid_limit", "max-running must be 1..100");
            }
            if self.active_count()? > limit {
                return fail(
                    "running_limit",
                    "stop clusters before lowering the limit below the active count",
                );
            }
            self.catalog.max_running = limit;
            self.save()?;
        }
        Ok(())
    }
    fn materialize_checkpoint(&mut self, c: &Checkpoint, i: &Instance) -> Result<()> {
        if let Some(id) = &c.manifest {
            let repo = self.repository()?;
            let compatibility = self.compatibility()?;
            self.new_generation(i)?;
            if self.use_cache {
                let bin = self.catalog.pg_bin.clone();
                let mut cache_events = vec![];
                let source = crate::cache::prepare(
                    &self.root.join("cache"),
                    &self.catalog.store_id,
                    id,
                    &repo,
                    &compatibility,
                    &mut cache_events,
                    |data| {
                        if data.join("postmaster.pid").exists() {
                            return fail("cache_running", "cache is not stopped");
                        }
                        let mut c = Command::new(bin.join("pg_controldata"));
                        c.arg(data);
                        let control = command(c)?;
                        if !control.lines().any(|l| {
                            l.starts_with("Database cluster state:")
                                && l.trim_end().ends_with("shut down")
                        }) {
                            return fail("cache_integrity", "cache is not cleanly stopped");
                        }
                        Ok(())
                    },
                )?;
                self.events.extend(cache_events);
                let t = Instant::now();
                copy_tree(&source, &self.data(i), self.full)?;
                sync_dir(&self.gen_dir(i))?;
                self.clean(i)?;
                self.record(
                    if self.full {
                        "cache_full_copy_and_sync"
                    } else {
                        "cache_apfs_clone_and_sync"
                    },
                    t,
                );
                failpoint("cache_branch_copied");
            } else {
                let t = Instant::now();
                repo.materialize(id, &compatibility, &self.data(i))?;
                self.clean(i)?;
                self.record("object_materialization", t);
            }
            Ok(())
        } else {
            self.copy(&c.instance, i)
        }
    }
    pub fn export(&mut self, name: &str, output: &Path) -> Result<()> {
        let c = self.ready_checkpoint(name)?;
        let id=c.manifest.ok_or_else(||Error::new("legacy_checkpoint","branch from this legacy checkpoint and create a new object checkpoint before exporting"))?;
        let parent = output
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        if fs::canonicalize(parent)?.starts_with(&self.root) {
            return fail("unsafe_path", "export outside the managed store");
        }
        let t = Instant::now();
        self.repository()?
            .export(&id, &self.compatibility()?, output)?;
        self.record("export_verify_copy_publish", t);
        self.events
            .push(json!({"stage":"export_storage","storage":storage_usage(output)?}));
        Ok(())
    }
    pub fn restore(&mut self, bundle: &Path, name: &str) -> Result<()> {
        self.unused(name)?;
        let compatibility = self.compatibility()?;
        let id = objects::validate_bundle(bundle, &compatibility)?;
        let mut i = Instance::new()?;
        self.catalog.branches.insert(
            name.into(),
            Branch {
                head: i.clone(),
                checkpoint: None,
                pending: None,
                resume: false,
                snapshot_stop: false,
                origin_manifest: Some(id),
            },
        );
        self.save()?;
        let result = (|| {
            self.new_generation(&i)?;
            let t = Instant::now();
            objects::restore_bundle(bundle, &compatibility, &self.data(&i))?;
            self.clean(&i)?;
            let mut checks = self.cmd("pg_checksums");
            checks.args(["--check", "-D"]).arg(self.data(&i));
            command(checks)?;
            self.record("restore_materialize_and_validate", t);
            failpoint("restore_before_publish");
            i.state = State::Stopped;
            self.persist_instance(name, false, &i)
        })();
        if result.is_err() {
            i.state = State::Failed;
            self.persist_instance(name, false, &i)?;
        }
        result
    }
    /// Preview or explicitly reclaim unreferenced generations and disposable cache.
    /// The store lock is held for planning, journaling and deletion. Catalog entries
    /// are never removed, including failed creations and legacy checkpoints.
    pub fn cleanup(&self, apply: bool) -> Result<Value> {
        #[derive(Serialize, Deserialize)]
        struct Journal {
            store_id: String,
            paths: Vec<String>,
        }
        fn hex(s: &str, n: usize) -> bool {
            s.len() == n
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        }
        fn cache_name(name: &str) -> bool {
            hex(name, 64)
                || [".building-", ".invalid-"].iter().any(|prefix| {
                    name.strip_prefix(prefix)
                        .and_then(|s| s.split_once('-'))
                        .is_some_and(|(id, suffix)| hex(id, 64) && valid_id(suffix))
                })
        }
        // Refuse all pidfiles, including stale or incomplete ones. Cleanup never
        // signals a server or guesses whether a partially created cluster is safe.
        fn safe_tree(path: &Path) -> Result<()> {
            let m = fs::symlink_metadata(path)?;
            if m.uid() != unsafe { libc::geteuid() }
                || m.permissions().mode() & 0o022 != 0
                || (!m.is_dir() && (!m.is_file() || m.nlink() != 1))
            {
                return fail(
                    "unsafe_path",
                    format!("unsafe cleanup path: {}", path.display()),
                );
            }
            if path.file_name().is_some_and(|n| n == "postmaster.pid") {
                return fail(
                    "not_stopped",
                    format!("cleanup refuses a pidfile: {}", path.display()),
                );
            }
            if m.is_dir() {
                for entry in fs::read_dir(path)? {
                    safe_tree(&entry?.path())?;
                }
            }
            Ok(())
        }
        let referenced: BTreeSet<_> = self
            .catalog
            .branches
            .values()
            .flat_map(|b| {
                std::iter::once(b.head.id.as_str()).chain(b.pending.as_ref().map(|p| p.id.as_str()))
            })
            .chain(
                self.catalog
                    .checkpoints
                    .values()
                    .map(|c| c.instance.id.as_str()),
            )
            .collect();
        let journal_path = self.root.join("cleanup.json");
        let pending = fs::symlink_metadata(&journal_path).is_ok();
        let mut paths = if pending {
            safe_tree(&journal_path)?;
            let journal: Journal = read_json(&journal_path)?;
            if journal.store_id != self.catalog.store_id {
                return fail("unsafe_path", "cleanup journal belongs to another store");
            }
            journal.paths
        } else {
            Vec::new()
        };
        if !pending {
            for entry in fs::read_dir(self.root.join("generations"))? {
                let entry = entry?;
                let id = entry.file_name().to_string_lossy().into_owned();
                if !valid_id(&id) || referenced.contains(id.as_str()) {
                    continue;
                }
                directory(&entry.path())?;
                let owner: Owner = read_json(&entry.path().join("owner.json"))?;
                if owner.store_id != self.catalog.store_id || owner.generation != id {
                    return fail("unsafe_path", "cleanup generation ownership does not match");
                }
                paths.push(format!("generations/{id}"));
            }
            let cache = self.root.join("cache");
            if fs::symlink_metadata(&cache).is_ok() {
                directory(&cache)?;
                for entry in fs::read_dir(&cache)? {
                    let entry = entry?;
                    let name = entry.file_name().to_string_lossy().into_owned();
                    if !cache_name(&name) {
                        continue;
                    }
                    directory(&entry.path())?;
                    let marker = entry.path().join("entry.json");
                    if fs::symlink_metadata(&marker).is_ok() {
                        let value: Value = read_json(&marker)?;
                        let id = name
                            .strip_prefix(".building-")
                            .or_else(|| name.strip_prefix(".invalid-"))
                            .map(|s| &s[..64])
                            .unwrap_or(&name);
                        if value["format"] != 1
                            || value["store_id"] != self.catalog.store_id
                            || value["manifest"] != id
                        {
                            return fail("unsafe_path", "cleanup cache ownership does not match");
                        }
                    } else if !name.starts_with(".building-") {
                        return fail("unsafe_path", "cleanup cache ownership marker is missing");
                    }
                    paths.push(format!("cache/{name}"));
                }
            }
        }
        paths.sort();
        paths.dedup();
        let mut candidates = Vec::new();
        let mut bytes = 0;
        // Validate the entire plan before deleting anything. A persisted journal
        // permits partially removed trees, but never paths now selected by catalog.
        for relative in &paths {
            let valid = relative
                .split_once('/')
                .is_some_and(|(kind, name)| match kind {
                    "generations" => valid_id(name) && !referenced.contains(name),
                    "cache" => cache_name(name),
                    _ => false,
                });
            if !valid {
                return fail("unsafe_path", "unsafe or referenced cleanup journal path");
            }
            let path = self.root.join(relative);
            let parent = path.parent().unwrap();
            directory(parent)?;
            let m = fs::metadata(parent)?;
            if m.uid() != unsafe { libc::geteuid() } || m.permissions().mode() & 0o022 != 0 {
                return fail("unsafe_path", "unsafe cleanup parent");
            }
            match fs::symlink_metadata(&path) {
                Err(e) if e.kind() == io::ErrorKind::NotFound && pending => continue,
                Err(e) => return Err(e.into()),
                Ok(_) => {}
            }
            directory(&path)?;
            safe_tree(&path)?;
            // A journal permits missing markers after partial removal, never a
            // conflicting marker that is still present.
            if pending {
                let (kind, name) = relative.split_once('/').unwrap();
                let marker = path.join(if kind == "generations" {
                    "owner.json"
                } else {
                    "entry.json"
                });
                if fs::symlink_metadata(&marker).is_ok() {
                    let value: Value = read_json(&marker)?;
                    let matches = if kind == "generations" {
                        value["store_id"] == self.catalog.store_id && value["generation"] == name
                    } else {
                        let id = name
                            .strip_prefix(".building-")
                            .or_else(|| name.strip_prefix(".invalid-"))
                            .map(|s| &s[..64])
                            .unwrap_or(name);
                        value["format"] == 1
                            && value["store_id"] == self.catalog.store_id
                            && value["manifest"] == id
                    };
                    if !matches {
                        return fail(
                            "unsafe_path",
                            "cleanup journal path ownership does not match",
                        );
                    }
                }
            }
            let usage = storage_usage(&path)?;
            bytes += usage.apparent_bytes;
            candidates.push(json!({"path":relative,"storage":usage}));
        }
        if apply && !paths.is_empty() {
            atomic_json(
                &journal_path,
                &Journal {
                    store_id: self.catalog.store_id.clone(),
                    paths: paths.clone(),
                },
            )?;
            failpoint("cleanup_journaled");
            for relative in &paths {
                let path = self.root.join(relative);
                if fs::symlink_metadata(&path).is_ok() {
                    safe_tree(&path)?;
                    fs::remove_dir_all(&path)?;
                    sync_dir(path.parent().unwrap())?;
                    failpoint("cleanup_removed");
                }
            }
        }
        if apply && (pending || !paths.is_empty()) {
            fs::remove_file(&journal_path)?;
            sync_dir(&self.root)?;
        }
        Ok(
            json!({"applied":apply,"resuming":pending,"candidates":candidates,"apparent_bytes":bytes,
            "note":"file sizes are not unique APFS space freed; checkpoints, selected/pending generations, objects and external exports are retained"}),
        )
    }
    pub fn stats(&self) -> Result<Value> {
        let mut result = json!({"total":storage_usage(&self.root)?,"generations":storage_usage(&self.root.join("generations"))?,"objects":null,"note":"apparent bytes and st_blocks are not unique APFS physical storage; totals include old generations, orphan objects and temporary files; exports outside this store are separate"});
        if self.root.join("objects").exists() {
            result["objects"] = serde_json::to_value(storage_usage(&self.root.join("objects"))?)?;
        }
        result["cache"] = if self.root.join("cache").exists() {
            serde_json::to_value(storage_usage(&self.root.join("cache"))?)?
        } else {
            Value::Null
        };
        Ok(result)
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
                snapshot_stop: false,
                origin_manifest: None,
            },
        );
        self.save()?;
        self.materialize_checkpoint(&c, &i)?;
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
        b.origin_manifest = checkpoint
            .as_ref()
            .and_then(|n| self.catalog.checkpoints.get(n))
            .and_then(|c| c.manifest.clone());
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
            self.materialize_checkpoint(&c, &i)?;
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
        let b = self.get(name)?;
        let at_capacity = self.active_count()? >= self.catalog.max_running;
        if at_capacity && self.observe(&b.head)?.is_none() {
            return fail("running_limit", "import needs one free server slot");
        }
        let mut i = self.begin_replace(name)?;
        let admission_interruption = at_capacity.then(Instant::now);
        let result = (|| {
            if at_capacity {
                self.stop_instance(name, false, b.head)?;
                failpoint("import_admission_stopped");
            }
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
        if let Some(t) = admission_interruption {
            self.record("import_admission_interruption", t);
        }
        result
    }
    pub fn reconcile(&mut self) -> Result<()> {
        let mut dirty = false;
        // Never publish interrupted creations. Retain their files for inspection.
        for c in self.catalog.checkpoints.values_mut() {
            if c.instance.state == State::Creating {
                c.instance.state = State::Failed;
                dirty = true;
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
                dirty = true;
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
            let previous = &self.catalog.branches[&name].head;
            if previous.state != head.state || previous.process != head.process {
                dirty = true;
            }
            self.catalog.branches.get_mut(&name).unwrap().head = head;
            if self.get(&name)?.snapshot_stop {
                if dirty {
                    self.save()?;
                    dirty = false;
                }
                self.stop(&name)?;
                self.catalog.branches.get_mut(&name).unwrap().snapshot_stop = false;
                self.save()?;
            }
            if self.get(&name)?.resume {
                if dirty {
                    self.save()?;
                    dirty = false;
                }
                if let Err(e) = self.start(&name) {
                    if e.code == "running_limit" {
                        self.events
                            .push(json!({"stage":"resume_deferred_capacity","branch":name}));
                        continue;
                    }
                    return Err(e);
                }
                self.catalog.branches.get_mut(&name).unwrap().resume = false;
                self.save()?;
            }
        }
        if dirty {
            self.save()?;
        }
        Ok(())
    }
    pub fn connection(&self, name: &str) -> Result<Value> {
        let b = self.get(name)?;
        if self.observe(&b.head)?.is_none() {
            return fail("not_running", "branch is stopped; run start first");
        }
        let socket = self.socket(&b.head);
        let encoded: String = socket
            .as_os_str()
            .as_encoded_bytes()
            .iter()
            .map(|b| format!("%{b:02X}"))
            .collect();
        Ok(json!({
            "connection":format!("host={} port=5432 user=forklift dbname=postgres",socket.display()),
            "uri":format!("postgresql://forklift@/postgres?host={encoded}&port=5432")
        }))
    }
    pub fn view(&self) -> Result<Value> {
        let branches:Vec<_>=self.catalog.branches.iter().map(|(name,b)|json!({"name":name,"state":b.head.state,"activity":if b.head.state==State::Stopped{"parked"}else if b.head.state==State::Running{"running"}else{"unavailable"},"resume_pending":b.resume,"checkpoint":b.checkpoint,"generation":b.head.id,"process":b.head.process,"origin_manifest":b.origin_manifest})).collect();
        let checkpoints:Vec<_>=self.catalog.checkpoints.iter().map(|(name,c)|json!({"name":name,"state":c.instance.state,"source":c.source,"copy_mode":c.copy_mode,"manifest":c.manifest})).collect();
        Ok(
            json!({"branches":branches,"checkpoints":checkpoints,"copy_mode":if self.full{"full"}else{"apfs"},"events":self.events,"max_running":self.catalog.max_running,"active_clusters":self.active_count()?,"cache_enabled":self.use_cache}),
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
