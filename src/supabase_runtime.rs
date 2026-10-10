//! Adapter for the exact experimental native Supabase CLI runtime we validate.
//! Raw start/status values contain local credentials and must stay private.
use crate::lifecycle::{Error, Result, fail};
use crate::storage::{directory, regular};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

pub const CLI_VERSION: &str = "2.120.0";
const PG_ARTIFACT: &str = "17.11.0.004-r1/darwin-arm64";
const EXCLUDED: &str = "functions,studio,mail,analytics,pooler";

#[derive(Clone, Debug, Serialize)]
pub struct SnapshotPaths {
    pub stacks_root: PathBuf,
    pub stack_root: PathBuf,
    pub state_file: PathBuf,
    pub database_root: PathBuf,
    pub pgdata: PathBuf,
    pub uploads_root: PathBuf,
    pub uploads: PathBuf,
    pub pg_bin: PathBuf,
    pub stack_id: String,
}
pub struct Runtime {
    cli: PathBuf,
    home: PathBuf,
    project: PathBuf,
    verifier_bin: PathBuf,
    cli_hash: String,
}
fn private_directory(path: &Path) -> Result<()> {
    directory(path)?;
    let m = fs::metadata(path)?;
    if m.uid() != unsafe { libc::geteuid() } || m.permissions().mode() & 0o077 != 0 {
        return fail(
            "supabase_path",
            "runtime home and project must be private directories owned by this user",
        );
    }
    Ok(())
}
fn safe_child(root: &Path, path: &Path) -> Result<PathBuf> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| Error::new("supabase_path", "runtime path escapes its private root"))?;
    let mut current = root.to_owned();
    for part in relative.components() {
        if !matches!(part, std::path::Component::Normal(_)) {
            return fail("supabase_path", "invalid runtime path");
        }
        current.push(part);
        if let Ok(m) = fs::symlink_metadata(&current)
            && m.file_type().is_symlink()
        {
            return fail("supabase_path", "runtime data paths cannot traverse links");
        }
    }
    Ok(current)
}
fn hash_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut buf = [0u8; 128 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hash.update(&buf[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn read_json(path: &Path) -> Result<Value> {
    regular(path)?;
    if fs::metadata(path)?.len() > 16 * 1024 * 1024 {
        return fail("supabase_metadata", "runtime metadata too large");
    }
    serde_json::from_slice(&fs::read(path)?)
        .map_err(|_| Error::new("supabase_metadata", "invalid runtime metadata JSON"))
}
fn sanitized(program: &Path) -> Command {
    let mut c = Command::new(program);
    for (key, _) in std::env::vars_os() {
        let k = key.to_string_lossy();
        if k.starts_with("SUPABASE")
            || k.starts_with("PG")
            || k.starts_with("GOTRUE")
            || k.starts_with("PGRST")
            || k.starts_with("DYLD_")
            || k.starts_with("LD_")
            || k.starts_with("OTEL_")
            || ["DATABASE_URL", "NODE_OPTIONS", "BUN_OPTIONS"].contains(&k.as_ref())
        {
            c.env_remove(key);
        }
    }
    c.env("DO_NOT_TRACK", "1")
        .env("LC_ALL", "C")
        .stdin(Stdio::null());
    c
}
impl Runtime {
    pub fn new(cli: &Path, home: &Path, project: &Path, pg_bin: &Path) -> Result<Self> {
        if std::env::consts::OS != "macos" || std::env::consts::ARCH != "aarch64" {
            return fail(
                "supabase_platform",
                "native Supabase backend currently requires macOS ARM64",
            );
        }
        private_directory(home)?;
        private_directory(project)?;
        regular(cli)?;
        let runtime = Self {
            cli: fs::canonicalize(cli)?,
            home: fs::canonicalize(home)?,
            project: fs::canonicalize(project)?,
            verifier_bin: fs::canonicalize(pg_bin)?,
            cli_hash: hash_file(cli)?,
        };
        runtime.check_identity()?;
        for tool in ["pg_controldata", "pg_checksums"] {
            let mut c = sanitized(&runtime.verifier_bin.join(tool));
            c.arg("--version");
            let out = c.output()?;
            if !out.status.success()
                || !String::from_utf8_lossy(&out.stdout).contains("(PostgreSQL) 17.")
            {
                return fail(
                    "supabase_postgres",
                    "PostgreSQL 17 control/checksum utilities are required",
                );
            }
        }
        Ok(runtime)
    }
    fn command(&self) -> Command {
        let mut c = sanitized(&self.cli);
        c.env("SUPABASE_HOME", &self.home)
            .env("SUPABASE_EXPERIMENTAL_STACK", "1")
            .env("SUPABASE_NO_KEYRING", "1")
            .current_dir(&self.project);
        c
    }
    pub fn check_identity(&self) -> Result<()> {
        if hash_file(&self.cli)? != self.cli_hash {
            return fail(
                "supabase_version",
                "Supabase CLI changed during this operation",
            );
        }
        let out = self.command().arg("--version").output()?;
        if !out.status.success() || String::from_utf8_lossy(&out.stdout).trim() != CLI_VERSION {
            return fail(
                "supabase_version",
                "this backend requires exactly Supabase CLI 2.120.0",
            );
        }
        Ok(())
    }
    fn invoke(&self, args: &[&str]) -> Result<Value> {
        self.check_identity()?;
        let out = self
            .command()
            .arg("--workdir")
            .arg(&self.project)
            .args(["--output-format", "json"])
            .args(args)
            .output()?;
        if !out.status.success() {
            return Err(self.private_failure(
                "local Supabase command failed",
                &out.stdout,
                &out.stderr,
            )?);
        }
        match serde_json::from_slice(&out.stdout) {
            Ok(value) => Ok(value),
            Err(_) => Err(self.private_failure(
                "local Supabase returned unexpected JSON",
                &out.stdout,
                &out.stderr,
            )?),
        }
    }
    // CLI output can include local service keys. Publish only an owner-readable
    // diagnostic file, never a raw error string or a truncate of an existing inode.
    fn private_failure(&self, message: &str, stdout: &[u8], stderr: &[u8]) -> Result<Error> {
        private_directory(&self.home)?;
        let path = self.home.join("last-error.log");
        match fs::symlink_metadata(&path) {
            Ok(m) if !m.is_file() || m.nlink() != 1 || m.uid() != unsafe { libc::geteuid() } => {
                return fail("supabase_path", "unsafe private diagnostic file");
            }
            Ok(_) => (),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(e.into()),
        }
        let temp = self
            .home
            .join(format!("last-error-{}.tmp", crate::storage::random_id()?));
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&temp)?;
        file.write_all(b"private local runtime diagnostic; may contain credentials\n\nstdout:\n")?;
        file.write_all(&stdout[..stdout.len().min(16 * 1024 * 1024)])?;
        file.write_all(b"\n\nstderr:\n")?;
        file.write_all(&stderr[..stderr.len().min(16 * 1024 * 1024)])?;
        file.sync_all()?;
        fs::rename(&temp, &path)?;
        crate::storage::sync_dir(&self.home)?;
        Ok(Error::new(
            "supabase_runtime",
            format!(
                "{message}; credential-bearing details saved privately at {}",
                path.display()
            ),
        ))
    }
    /// Contains credentials. The caller must never include this raw value in logs.
    pub fn status(&self) -> Result<Value> {
        let value = self.invoke(&["status"])?;
        self.validate_status(&value)?;
        Ok(value)
    }
    fn validate_status(&self, value: &Value) -> Result<()> {
        if value["runtime"] != "native"
            || value["identity"]["project_root"].as_str() != self.project.to_str()
        {
            return fail(
                "supabase_identity",
                "runtime is not the expected private native project",
            );
        }
        Ok(())
    }
    fn launch(&self) -> Result<Value> {
        self.invoke(&[
            "start",
            "--runtime",
            "native",
            "--preparation",
            "on-demand",
            "--eager",
            "--exclude",
            EXCLUDED,
        ])?;
        let status = self.status()?;
        if status["lifecycle"] != "running" || status["readiness"] != "ready" {
            return fail("supabase_runtime", "native Supabase services are not ready");
        }
        let services = status["services"]
            .as_array()
            .ok_or_else(|| Error::new("supabase_runtime", "missing native service status"))?;
        for name in ["database", "rest", "auth", "storage", "realtime"] {
            if !services.iter().any(|s| {
                s["service"] == name && s["state"] == "running" && s["health"] == "healthy"
            }) {
                return fail(
                    "supabase_runtime",
                    "a required native Supabase service is not healthy",
                );
            }
        }
        Ok(status)
    }
    /// Start the core local stack, enabling native initdb's initially disabled checksums offline.
    pub fn start(&self) -> Result<Value> {
        let mut status = self.launch()?;
        let settings = self.sql("SELECT current_setting('fsync') || ',' || current_setting('full_page_writes') || ',' || current_setting('synchronous_commit') || ',' || current_setting('data_checksums')")?;
        if settings == "on,on,on,off" {
            self.invoke(&["stop"])?;
            self.verify_shutdown(false)?;
            let paths = self.snapshot_paths()?;
            let out = sanitized(&self.verifier_bin.join("pg_checksums"))
                .args(["--enable", "-D"])
                .arg(&paths.pgdata)
                .output()?;
            if !out.status.success() {
                return fail(
                    "supabase_checksums",
                    "could not enable checksums on the stopped local database",
                );
            }
            status = self.launch()?;
        } else if settings != "on,on,on,on" {
            let _ = self.invoke(&["stop"]);
            return fail(
                "durability",
                "native Supabase must keep fsync, full_page_writes and synchronous_commit enabled",
            );
        }
        self.verify_durability()?;
        Ok(status)
    }
    pub fn verify_durability(&self) -> Result<()> {
        let settings = self.sql("SELECT current_setting('fsync') || ',' || current_setting('full_page_writes') || ',' || current_setting('synchronous_commit') || ',' || current_setting('data_checksums')")?;
        if settings != "on,on,on,on" {
            return fail(
                "durability",
                "native Supabase durability settings or checksums are disabled",
            );
        }
        Ok(())
    }
    pub fn stop(&self) -> Result<Value> {
        self.invoke(&["stop"])?;
        // An already-stopped owner is reported as `unavailable` by this CLI.
        // Accept it only after independently checking owner/pidfile absence,
        // a clean control-file shutdown and all database page checksums.
        self.verify_stopped()?;
        Ok(json!({"stopped":true,"runtime":"native"}))
    }
    pub fn verify_stopped(&self) -> Result<()> {
        self.verify_shutdown(true)
    }
    fn verify_shutdown(&self, require_checksums: bool) -> Result<()> {
        let status = self.status()?;
        if status["owner"] != "unavailable" || !status["lifecycle"].is_null() {
            return fail(
                "supabase_running",
                "stop every native Supabase service before snapshotting",
            );
        }
        let paths = self.snapshot_paths()?;
        if paths.pgdata.join("postmaster.pid").try_exists()?
            || paths.stack_root.join("owner.json").try_exists()?
        {
            return fail(
                "supabase_running",
                "runtime still has a server identity file",
            );
        }
        let out = sanitized(&self.verifier_bin.join("pg_controldata"))
            .arg(&paths.pgdata)
            .output()?;
        if !out.status.success() {
            return fail(
                "supabase_control",
                "cannot verify stopped PostgreSQL control data",
            );
        }
        let control = String::from_utf8_lossy(&out.stdout);
        if !control.lines().any(|l| {
            l.starts_with("Database cluster state:") && l.trim_end().ends_with("shut down")
        }) {
            return fail(
                "supabase_running",
                "native PostgreSQL is not cleanly shut down",
            );
        }
        if require_checksums
            && !control.lines().any(|l| {
                l.starts_with("Data page checksum version:") && l.trim_end().ends_with("1")
            })
        {
            return fail(
                "supabase_checksums",
                "native PostgreSQL data checksums must be enabled",
            );
        }
        if require_checksums {
            let out = sanitized(&self.verifier_bin.join("pg_checksums"))
                .args(["--check", "-D"])
                .arg(&paths.pgdata)
                .output()?;
            if !out.status.success() {
                return fail(
                    "supabase_checksums",
                    "native PostgreSQL page checksum verification failed",
                );
            }
        }
        Ok(())
    }
    /// Query only the private native PostgreSQL Unix socket as its local administrator.
    pub fn sql(&self, sql: &str) -> Result<String> {
        let paths = self.snapshot_paths()?;
        let pid = fs::read_to_string(paths.pgdata.join("postmaster.pid"))?;
        let lines: Vec<_> = pid.lines().collect();
        if lines.len() < 5 || Path::new(lines[1]) != paths.pgdata || lines[3] != "5432" {
            return fail(
                "supabase_identity",
                "native PostgreSQL pidfile does not match its data directory",
            );
        }
        let mut c = sanitized(&paths.pg_bin.join("psql"));
        c.env("PGCONNECT_TIMEOUT", "3").args([
            "-h",
            lines[4],
            "-p",
            "5432",
            "-U",
            "supabase_admin",
            "-d",
            "postgres",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT current_setting('data_directory')",
            "-c",
            sql,
        ]);
        let out = c.output()?;
        if !out.status.success() {
            return fail(
                "supabase_sql",
                "private native PostgreSQL query failed; output suppressed",
            );
        }
        let text = String::from_utf8_lossy(&out.stdout);
        let (data, result) = text.split_once('\n').ok_or_else(|| {
            Error::new(
                "supabase_identity",
                "missing native PostgreSQL identity response",
            )
        })?;
        if Path::new(data) != paths.pgdata {
            return fail(
                "supabase_identity",
                "connected PostgreSQL does not own this private data directory",
            );
        }
        Ok(result.trim().to_owned())
    }
    pub fn snapshot_paths(&self) -> Result<SnapshotPaths> {
        let stacks_root = safe_child(&self.home, &self.home.join("stacks"))?;
        directory(&stacks_root)?;
        let mut matches = Vec::new();
        for entry in fs::read_dir(&stacks_root)? {
            let path = entry?.path();
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if name.len() != 64
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            {
                continue;
            }
            safe_child(&self.home, &path)?;
            let state_file = safe_child(&self.home, &path.join("state.json"))?;
            let state = read_json(&state_file)?;
            if state["identity"]["projectRoot"].as_str() == self.project.to_str() {
                if state["id"] != name || state["runtime"] != "native" {
                    return fail("supabase_identity", "native stack identity mismatch");
                }
                matches.push((path, state_file, state));
            } else {
                return fail(
                    "supabase_identity",
                    "private runtime home contains another project's stack; refusing to snapshot or rewind it",
                );
            }
        }
        if matches.len() != 1 {
            return fail(
                "supabase_identity",
                "expected exactly one private native stack for this project",
            );
        }
        let (stack_root, state_file, state) = matches.pop().unwrap();
        let stack_id = state["id"].as_str().unwrap().to_owned();
        let instances = state["instances"]
            .as_array()
            .ok_or_else(|| Error::new("supabase_metadata", "missing service instances"))?;
        let databases: Vec<_> = instances
            .iter()
            .filter(|i| i["creation"]["service"] == "database")
            .collect();
        let storages: Vec<_> = instances
            .iter()
            .filter(|i| i["creation"]["service"] == "storage")
            .collect();
        if databases.len() != 1 || storages.len() != 1 {
            return fail(
                "supabase_identity",
                "expected one native database and storage service",
            );
        }
        let id = databases[0]["id"]
            .as_str()
            .ok_or_else(|| Error::new("supabase_metadata", "missing database identity"))?;
        if id.is_empty() || !id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
            return fail("supabase_identity", "invalid native database identity");
        }
        let database_root = safe_child(&self.home, &stack_root.join("data").join(id))?;
        let owner = read_json(&database_root.join(".supabase-database-owner.json"))?;
        if owner["stackId"] != stack_id || owner["instanceId"] != id {
            return fail("supabase_identity", "native database ownership mismatch");
        }
        let pgdata = safe_child(&self.home, &database_root.join("data"))?;
        if fs::read_to_string(pgdata.join("PG_VERSION"))?.trim() != "17" {
            return fail("supabase_version", "native PostgreSQL major must be 17");
        }
        let uploads_root = safe_child(
            &self.project,
            &self.project.join("supabase/.temp/stack-uploads"),
        )?;
        let uploads = safe_child(&self.project, &uploads_root.join(&stack_id))?;
        if storages[0]["creation"]["config"]["filePath"].as_str() != uploads.to_str() {
            return fail(
                "supabase_identity",
                "storage uploads escape the private project",
            );
        }
        let pg_bin = safe_child(
            &self.home,
            &self
                .home
                .join("cache/stack/slim-services/postgres")
                .join(PG_ARTIFACT)
                .join("bin"),
        )?;
        regular(&pg_bin.join("psql"))?;
        Ok(SnapshotPaths {
            stacks_root,
            stack_root,
            state_file,
            database_root,
            pgdata,
            uploads_root,
            uploads,
            pg_bin,
            stack_id,
        })
    }
    /// Exact runtime content identity, excluding mutable stack state and credentials.
    pub fn fingerprint(&self) -> Result<Value> {
        self.check_identity()?;
        let root = self.home.join("cache/stack/slim-services");
        directory(&root)?;
        let canonical_root = fs::canonicalize(&root)?;
        fn walk(
            root: &Path,
            at: &Path,
            hash: &mut Sha256,
            count: &mut u64,
            targets: &mut std::collections::BTreeMap<PathBuf, String>,
        ) -> Result<()> {
            let mut children = fs::read_dir(at)?
                .map(|e| e.map(|e| e.path()))
                .collect::<std::io::Result<Vec<_>>>()?;
            children.sort();
            for path in children {
                let m = fs::symlink_metadata(&path)?;
                if m.is_dir() {
                    walk(root, &path, hash, count, targets)?;
                } else {
                    let target = fs::canonicalize(&path)?;
                    if !target.starts_with(root) || !target.is_file() {
                        return fail(
                            "supabase_runtime",
                            "runtime artifact has an external or unsupported link",
                        );
                    }
                    let relative = path
                        .strip_prefix(root)
                        .unwrap()
                        .as_os_str()
                        .as_encoded_bytes();
                    hash.update((relative.len() as u64).to_le_bytes());
                    hash.update(relative);
                    // Native artifacts contain many aliases of the same library.
                    // Reuse a target's digest only within this complete scan.
                    if !targets.contains_key(&target) {
                        targets.insert(target.clone(), hash_file(&target)?);
                    }
                    hash.update(&targets[&target]);
                    *count += 1;
                }
            }
            Ok(())
        }
        let mut hash = Sha256::new();
        let mut count = 0;
        walk(
            &canonical_root,
            &canonical_root,
            &mut hash,
            &mut count,
            &mut std::collections::BTreeMap::new(),
        )?;
        Ok(
            json!({"cli_version":CLI_VERSION,"cli_sha256":self.cli_hash,"platform":"macos-aarch64","postgres_artifact":PG_ARTIFACT,"artifacts_sha256":format!("{:x}",hash.finalize()),"artifact_files":count,
            "pg_controldata_sha256":hash_file(&self.verifier_bin.join("pg_controldata"))?,"pg_checksums_sha256":hash_file(&self.verifier_bin.join("pg_checksums"))?}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn diagnostics_keep_credentials_private_and_refuse_links() -> Result<()> {
        let home = std::env::temp_dir().join(format!(
            "forklift-native-log-{}",
            crate::storage::random_id()?
        ));
        crate::storage::private_dir(&home)?;
        let runtime = Runtime {
            cli: PathBuf::new(),
            home: home.clone(),
            project: home.clone(),
            verifier_bin: PathBuf::new(),
            cli_hash: String::new(),
        };
        let error =
            runtime.private_failure("command failed", b"secret-fixture-token", b"diagnostic")?;
        assert!(!error.message.contains("secret-fixture-token"));
        let log = home.join("last-error.log");
        assert_eq!(fs::metadata(&log)?.permissions().mode() & 0o777, 0o600);
        assert!(fs::read_to_string(&log)?.contains("secret-fixture-token"));
        let linked = home.join("linked");
        fs::hard_link(&log, &linked)?;
        assert!(
            runtime
                .private_failure("command failed", b"replacement", b"")
                .is_err()
        );
        assert!(fs::read_to_string(&linked)?.contains("secret-fixture-token"));
        fs::remove_dir_all(home)?;
        Ok(())
    }
}
