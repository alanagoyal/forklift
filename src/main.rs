//! Milestone-one experiment, not the future branch-management CLI.
use std::{
    env,
    error::Error,
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    os::unix::{
        ffi::OsStrExt,
        fs::{MetadataExt, PermissionsExt},
    },
    path::{Path, PathBuf},
    process::Command,
    time::Instant,
};
type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn run(cmd: &mut Command) -> Result<String> {
    // Ambient PostgreSQL variables must not redirect clients to another database.
    for (key, _) in env::vars_os() {
        if key.to_string_lossy().starts_with("PG") {
            cmd.env_remove(key);
        }
    }
    let out = cmd.output()?;
    if !out.status.success() {
        return Err(format!("{cmd:?}: {}", String::from_utf8_lossy(&out.stderr)).into());
    }
    Ok(String::from_utf8(out.stdout)?.trim().to_owned())
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn clonefile(src: *const std::ffi::c_char, dst: *const std::ffi::c_char, flags: u32) -> i32;
}
fn clone_file(src: &Path, dst: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let s = CString::new(src.as_os_str().as_bytes())?;
        let d = CString::new(dst.as_os_str().as_bytes())?;
        // C strings remain alive for the call. Failure never falls back to copying.
        if unsafe { clonefile(s.as_ptr(), d.as_ptr(), 0) } != 0 {
            return Err(io::Error::last_os_error().into());
        }
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (src, dst);
        Err("native clone benchmark currently requires macOS".into())
    }
}

fn copy_tree(src: &Path, dst: &Path, clone: bool) -> Result<()> {
    fs::create_dir(dst)?; // Refuse replacement, including a previous failed run.
    fs::set_permissions(dst, fs::metadata(src)?.permissions())?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let to = dst.join(entry.file_name());
        let kind = entry.file_type()?;
        if kind.is_dir() {
            copy_tree(&entry.path(), &to, clone)?;
        } else if kind.is_file() {
            if clone {
                clone_file(&entry.path(), &to)?;
            } else {
                // fs::copy can itself use APFS clones. Explicit buffered I/O is the full-copy baseline.
                let mut input = File::open(entry.path())?;
                let mut output = OpenOptions::new().write(true).create_new(true).open(&to)?;
                let mut buffer = [0u8; 128 * 1024];
                loop {
                    let n = std::io::Read::read(&mut input, &mut buffer)?;
                    if n == 0 {
                        break;
                    }
                    output.write_all(&buffer[..n])?;
                }
                fs::set_permissions(&to, entry.metadata()?.permissions())?;
            }
            File::open(to)?.sync_all()?;
        } else {
            return Err("links and special files are unsupported".into());
        }
    }
    File::open(dst)?.sync_all()?;
    Ok(())
}
fn size(path: &Path) -> Result<(u64, u64)> {
    let mut total = (0, 0);
    for e in fs::read_dir(path)? {
        let e = e?;
        let m = e.metadata()?;
        let s = if m.is_dir() {
            size(&e.path())?
        } else {
            (m.len(), m.blocks() * 512)
        };
        total.0 += s.0;
        total.1 += s.1;
    }
    Ok(total)
}
struct Lab {
    bin: PathBuf,
    root: PathBuf,
    socket: PathBuf,
    clusters: Vec<String>,
}
impl Lab {
    fn cmd(&self, name: &str) -> Command {
        Command::new(self.bin.join(name))
    }
    fn init(&mut self, name: &str) -> Result<()> {
        self.clusters.push(name.into());
        run(self
            .cmd("initdb")
            .args(["-D"])
            .arg(self.root.join(name))
            .args([
                "--no-locale",
                "--encoding=UTF8",
                "--data-checksums",
                "--auth-local=trust",
                "--auth-host=reject",
                "-U",
                "forklift",
            ]))?;
        Ok(())
    }
    fn start(&self, name: &str, port: u16) -> Result<()> {
        // Socket directory is private; TCP is disabled, even for loopback.
        let options = format!(
            "-p {port} -k {} -c listen_addresses='' -c fsync=on -c full_page_writes=on -c synchronous_commit=on -c shared_buffers=32MB -c max_connections=20",
            self.socket.display()
        );
        run(self
            .cmd("pg_ctl")
            .arg("-D")
            .arg(self.root.join(name))
            .arg("-l")
            .arg(self.root.join(format!("{name}.log")))
            .args(["-w", "-t", "30", "-o", &options, "start"]))?;
        if self.sql(port, "SELECT current_setting('fsync') || ',' || current_setting('full_page_writes') || ',' || current_setting('synchronous_commit') || ',' || current_setting('data_checksums')")? != "on,on,on,on" { return Err("durability assertion failed".into()); }
        Ok(())
    }
    fn stop(&self, name: &str) -> Result<()> {
        run(self
            .cmd("pg_ctl")
            .arg("-D")
            .arg(self.root.join(name))
            .args(["-w", "-t", "30", "-m", "fast", "stop"]))?;
        if self.root.join(name).join("postmaster.pid").exists() {
            return Err("postmaster.pid remains after stop".into());
        }
        let control = run(self
            .cmd("pg_controldata")
            .arg(self.root.join(name))
            .env("LC_ALL", "C"))?;
        if !control.lines().any(|l| {
            l.starts_with("Database cluster state:") && l.trim_end().ends_with("shut down")
        }) {
            return Err("cluster is not cleanly shut down".into());
        }
        Ok(())
    }
    fn client(&self, tool: &str, port: u16) -> Command {
        let mut c = self.cmd(tool);
        c.arg("-h").arg(&self.socket).args([
            "-p",
            &port.to_string(),
            "-U",
            "forklift",
            "-d",
            "postgres",
        ]);
        c
    }
    fn sql(&self, port: u16, sql: &str) -> Result<String> {
        run(self
            .client("psql", port)
            .args(["-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql]))
    }
    fn script(&self, port: u16, file: &str, rows: usize) -> Result<()> {
        run(self.client("psql", port).args([
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            &format!("rows={rows}"),
            "-f",
            file,
        ]))?;
        Ok(())
    }
    fn digest(&self, port: u16) -> Result<String> {
        self.sql(port, "SELECT count(*) || ':' || md5(string_agg(md5(row_to_json(a)::text), '' ORDER BY id)) FROM accounts a")
    }
    fn schema(&self, port: u16) -> Result<String> {
        self.sql(port, "SELECT md5(string_agg(v, '|' ORDER BY v)) FROM (SELECT column_name || ':' || data_type || ':' || is_nullable || ':' || coalesce(column_default,'') AS v FROM information_schema.columns WHERE table_schema='public' AND table_name='accounts' UNION ALL SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='accounts') s")
    }
    fn verify(&self, port: u16, data: &str, schema: &str) -> Result<()> {
        if self.digest(port)? != data || self.schema(port)? != schema {
            return Err("data/schema checksum mismatch".into());
        }
        Ok(())
    }
}
impl Drop for Lab {
    fn drop(&mut self) {
        for name in &self.clusters {
            if self.root.join(name).join("postmaster.pid").exists()
                && let Err(e) = self.stop(name)
            {
                eprintln!("cleanup failed for {name}: {e}");
            }
        }
        if let Err(e) = fs::remove_dir(&self.socket) {
            eprintln!("socket cleanup: {e}");
        }
    }
}
fn metric(
    file: &mut File,
    trial: usize,
    method: &str,
    stage: &str,
    value: impl std::fmt::Display,
    unit: &str,
) -> Result<()> {
    writeln!(file, "{trial}\t{method}\t{stage}\t{value}\t{unit}")?;
    file.sync_all()?;
    Ok(())
}
fn seconds(file: &mut File, trial: usize, method: &str, stage: &str, start: Instant) -> Result<()> {
    metric(
        file,
        trial,
        method,
        stage,
        format!("{:.6}", start.elapsed().as_secs_f64()),
        "seconds",
    )
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 5 {
        return Err("usage: forklift-feasibility NEW_RUN_DIRECTORY ROWS REPETITIONS PG_BIN\nExample: cargo run --release --bin forklift-feasibility -- benchmark-data/run-1 100000 3 /opt/homebrew/opt/postgresql@17/bin".into());
    }
    let rows: usize = args[2].parse()?;
    let trials: usize = args[3].parse()?;
    if !(1..=1_000_000).contains(&rows) || !(1..=20).contains(&trials) {
        return Err("rows must be 1..1000000, repetitions 1..20".into());
    }
    let bin = fs::canonicalize(&args[4])?;
    let version = run(Command::new(bin.join("postgres")).arg("--version"))?;
    if !version.starts_with("postgres (PostgreSQL) 17.") {
        return Err("PostgreSQL major 17 required".into());
    }
    let requested = PathBuf::from(&args[1]);
    if let Some(parent) = requested.parent()
        && !parent.as_os_str().is_empty()
    {
        fs::create_dir_all(parent)?;
    }
    fs::create_dir(&requested)?;
    let root = fs::canonicalize(requested)?;
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;
    let socket = PathBuf::from(format!("/tmp/forklift-{}", std::process::id()));
    fs::create_dir(&socket)?;
    fs::set_permissions(&socket, fs::Permissions::from_mode(0o700))?;
    let mut lab = Lab {
        bin,
        root,
        socket,
        clusters: vec![],
    };
    let mut out = File::create(lab.root.join("metrics.tsv"))?;
    writeln!(out, "trial\tmethod\tstage\tvalue\tunit")?;
    fs::write(
        lab.root.join("environment.txt"),
        format!(
            "postgres={version}\nrows={rows}\nrepetitions={trials}\nos={}\narchitecture={}\ncache=uncontrolled; sequential repeated runs\ndurability=fsync,full_page_writes,synchronous_commit,data_checksums:on\nshared_buffers=32MB\nmax_connections=20\nTCP=disabled\n",
            run(Command::new("sw_vers").arg("-productVersion"))?,
            env::consts::ARCH
        ),
    )?;
    // Capability probe uses the exact primitive used by the experiment.
    let probe = lab.root.join("probe");
    let probe_clone = lab.root.join("probe-clone");
    fs::write(&probe, b"original")?;
    clone_file(&probe, &probe_clone)?;
    if fs::metadata(&probe)?.ino() == fs::metadata(&probe_clone)?.ino() {
        return Err("clone unexpectedly shares an inode".into());
    }
    fs::write(&probe_clone, b"changed")?;
    if fs::read(&probe)? != b"original" {
        return Err("clone isolation probe failed".into());
    }
    fs::remove_file(probe)?;
    fs::remove_file(probe_clone)?;
    lab.init("seed")?;
    lab.start("seed", 55431)?;
    lab.script(55431, "fixtures/seed.sql", rows)?;
    let expected = lab.digest(55431)?;
    let schema = lab.schema(55431)?;
    fs::write(
        lab.root.join("checksums.txt"),
        format!("seed_data={expected}\nseed_schema={schema}\n"),
    )?;
    metric(
        &mut out,
        0,
        "seed",
        "database_bytes",
        lab.sql(55431, "SELECT pg_database_size(current_database())")?,
        "bytes",
    )?;
    metric(
        &mut out,
        0,
        "seed",
        "table_and_indexes_bytes",
        lab.sql(55431, "SELECT pg_total_relation_size('accounts')")?,
        "bytes",
    )?;
    lab.stop("seed")?;
    let (apparent, allocated) = size(&lab.root.join("seed"))?;
    metric(&mut out, 0, "seed", "apparent_bytes", apparent, "bytes")?;
    metric(
        &mut out,
        0,
        "seed",
        "st_blocks_not_unique_bytes",
        allocated,
        "bytes",
    )?;
    for trial in 1..=trials {
        for method in ["full-copy", "apfs-clone", "dump-restore"] {
            println!("trial {trial}/{trials}: {method}");
            let name = format!("{method}-{trial}");
            lab.start("seed", 55431)?;
            let total = Instant::now();
            if method == "dump-restore" {
                let start = Instant::now();
                run(lab
                    .client("pg_dump", 55431)
                    .args(["-Fc", "-f"])
                    .arg(lab.root.join(format!("{name}.dump"))))?;
                seconds(&mut out, trial, method, "dump", start)?;
                let start = Instant::now();
                lab.init(&name)?;
                seconds(&mut out, trial, method, "initdb", start)?;
                let start = Instant::now();
                lab.start(&name, 55432)?;
                seconds(
                    &mut out,
                    trial,
                    method,
                    "startup_including_durability_query",
                    start,
                )?;
                let start = Instant::now();
                run(lab
                    .client("pg_restore", 55432)
                    .arg("--exit-on-error")
                    .arg(lab.root.join(format!("{name}.dump"))))?;
                seconds(&mut out, trial, method, "restore", start)?;
            } else {
                let interruption = Instant::now();
                let start = Instant::now();
                lab.stop("seed")?;
                seconds(&mut out, trial, method, "source_stop", start)?;
                let start = Instant::now();
                lab.clusters.push(name.clone());
                copy_tree(
                    &lab.root.join("seed"),
                    &lab.root.join(&name),
                    method == "apfs-clone",
                )?;
                seconds(&mut out, trial, method, "copy_and_sync", start)?;
                lab.start("seed", 55431)?;
                seconds(&mut out, trial, method, "source_interruption", interruption)?;
                let start = Instant::now();
                lab.start(&name, 55432)?;
                seconds(
                    &mut out,
                    trial,
                    method,
                    "startup_including_durability_query",
                    start,
                )?;
            }
            let query = Instant::now();
            lab.verify(55432, &expected, &schema)?;
            seconds(&mut out, trial, method, "first_verified_query", query)?;
            seconds(&mut out, trial, method, "branch_to_verified_query", total)?;
            // Capture parent and child RSS; summed RSS counts shared mappings repeatedly.
            let processes = run(Command::new("ps").args(["-axo", "pid=,ppid=,rss=,comm="]))?;
            let parent: u32 = fs::read_to_string(lab.root.join(&name).join("postmaster.pid"))?
                .lines()
                .next()
                .ok_or("empty pidfile")?
                .parse()?;
            let selected: Vec<&str> = processes
                .lines()
                .filter(|l| {
                    let p: Vec<_> = l.split_whitespace().collect();
                    p.len() >= 3
                        && (p[0].parse::<u32>() == Ok(parent) || p[1].parse::<u32>() == Ok(parent))
                })
                .collect();
            fs::write(
                lab.root.join(format!("{name}-processes.txt")),
                selected.join("\n"),
            )?;
            let rss: u64 = selected
                .iter()
                .map(|l| l.split_whitespace().nth(2).unwrap().parse::<u64>().unwrap())
                .sum();
            metric(
                &mut out,
                trial,
                method,
                "post_query_process_count",
                selected.len(),
                "processes",
            )?;
            metric(
                &mut out,
                trial,
                method,
                "post_query_summed_rss_not_unique",
                rss,
                "KiB",
            )?;
            lab.script(55432, "fixtures/edit.sql", rows)?;
            let edited = lab.digest(55432)?;
            let edited_schema = lab.schema(55432)?;
            let mut checksums = OpenOptions::new()
                .append(true)
                .open(lab.root.join("checksums.txt"))?;
            writeln!(
                checksums,
                "{name}_data={edited}\n{name}_schema={edited_schema}"
            )?;
            if edited == expected || edited_schema == schema {
                return Err("edit did not change data and schema".into());
            }
            lab.verify(55431, &expected, &schema)?;
            lab.stop(&name)?;
            lab.start(&name, 55432)?;
            lab.verify(55432, &edited, &edited_schema)?;
            // A second physical branch applies a conflicting column type while A stays live.
            lab.stop("seed")?;
            let sibling = format!("sibling-{name}");
            lab.clusters.push(sibling.clone());
            copy_tree(&lab.root.join("seed"), &lab.root.join(&sibling), true)?;
            lab.start(&sibling, 55433)?;
            lab.verify(55433, &expected, &schema)?;
            lab.script(55433, "fixtures/conflict.sql", rows)?;
            let sibling_data = lab.digest(55433)?;
            let sibling_schema = lab.schema(55433)?;
            lab.verify(55432, &edited, &edited_schema)?;
            writeln!(
                checksums,
                "{sibling}_data={sibling_data}\n{sibling}_schema={sibling_schema}"
            )?;
            checksums.sync_all()?;
            lab.stop(&sibling)?;
            lab.start(&sibling, 55433)?;
            lab.verify(55433, &sibling_data, &sibling_schema)?;
            lab.stop(&sibling)?;
            lab.start("seed", 55431)?;
            lab.verify(55431, &expected, &schema)?;
            lab.stop("seed")?;
            lab.stop(&name)?;
            run(lab
                .cmd("pg_checksums")
                .arg("--check")
                .arg("-D")
                .arg(lab.root.join(&name)))?;
            run(lab
                .cmd("pg_checksums")
                .arg("--check")
                .arg("-D")
                .arg(lab.root.join(&sibling)))?;
            let (wal, _) = size(&lab.root.join(&name).join("pg_wal"))?;
            metric(
                &mut out,
                trial,
                method,
                "edited_wal_apparent_bytes",
                wal,
                "bytes",
            )?;
            let (apparent, allocated) = size(&lab.root.join(&name))?;
            metric(
                &mut out,
                trial,
                method,
                "edited_apparent_bytes",
                apparent,
                "bytes",
            )?;
            metric(
                &mut out,
                trial,
                method,
                "edited_st_blocks_not_unique_bytes",
                allocated,
                "bytes",
            )?;
            metric(
                &mut out,
                trial,
                method,
                "isolation_restart_and_page_checksums",
                1,
                "passed",
            )?;
        }
    }
    run(lab
        .cmd("pg_checksums")
        .arg("--check")
        .arg("-D")
        .arg(lab.root.join("seed")))?;
    fs::write(
        lab.root.join("SUCCESS"),
        "all data/schema isolation, clean restart and page checksum checks passed\n",
    )?;
    println!(
        "passed; results: {} (clusters retained, servers stopped)",
        lab.root.display()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn copy_refuses_replacement_and_unsafe_links() -> Result<()> {
        let root = env::temp_dir().join(format!("forklift-copy-test-{}", std::process::id()));
        fs::create_dir(&root)?;
        let src = root.join("source");
        fs::create_dir(&src)?;
        fs::write(src.join("data"), b"original")?;
        let dst = root.join("destination");
        copy_tree(&src, &dst, false)?;
        fs::write(dst.join("data"), b"edited")?;
        assert_eq!(fs::read(src.join("data"))?, b"original");
        assert!(copy_tree(&src, &dst, false).is_err());
        assert_eq!(fs::read(dst.join("data"))?, b"edited");
        std::os::unix::fs::symlink(src.join("data"), src.join("unsafe-link"))?;
        assert!(copy_tree(&src, &root.join("rejected"), false).is_err());
        fs::remove_dir_all(root)?;
        Ok(())
    }
}
