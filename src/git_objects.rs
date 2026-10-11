//! Ordinary Git blobs/trees/parentless commits in a private bare repository.
//! No worktree, filters, hooks, remotes, application commits, or live checkout.
use crate::{
    lifecycle::{Result, fail, failpoint},
    objects::{self, *},
    storage::*,
};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Instant,
};

const CONFIG: &[u8] = b"[core]\nrepositoryformatversion = 0\nbare = true\nfilemode = true\nlogallrefupdates = false\ncompression = 6\nfsync = committed\nfsyncMethod = fsync\n[gc]\nauto = 0\n";
pub(crate) fn valid_oid(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub struct Repository {
    root: PathBuf,
}
impl Repository {
    pub fn open(root: &Path) -> Result<Self> {
        if !root.try_exists()? {
            // Build privately, then publish once. An interrupted initializer is never opened.
            let stage = root.with_file_name(format!(".git-store-{}", random_id()?));
            private_dir(&stage)?;
            for sub in [
                "repo",
                "manifests",
                "repo/objects",
                "repo/objects/info",
                "repo/objects/pack",
                "repo/refs",
                "repo/refs/checkpoints",
            ] {
                private_dir(&stage.join(sub))?;
            }
            fs::write(stage.join("repo/config"), CONFIG)?;
            fs::write(stage.join("repo/HEAD"), b"ref: refs/heads/unused\n")?;
            sync_tree(&stage)?;
            publish_new(&stage, root)?;
        }
        let r = Self {
            root: root.to_owned(),
        };
        r.validate()?;
        Ok(r)
    }
    fn validate(&self) -> Result<()> {
        crate::retention::safe_tree(&self.root)?;
        if fs::read(self.root.join("repo/config"))? != CONFIG {
            return fail(
                "unsafe_git",
                "private Git config differs from the fixed Forklift config",
            );
        }
        for name in [
            "objects/info/alternates",
            "objects/info/http-alternates",
            "shallow",
            "info/grafts",
            "refs/replace",
            "commondir",
            "worktrees",
            "hooks",
        ] {
            if self.root.join("repo").join(name).try_exists()? {
                return fail("unsafe_git", format!("unsupported Git metadata: {name}"));
            }
        }
        if fs::read(self.root.join("repo/HEAD"))? != b"ref: refs/heads/unused\n" {
            return fail("unsafe_git", "private Git HEAD differs");
        }
        let refs = self.run(&["for-each-ref", "--format=%(refname)"], b"")?;
        if refs.lines().any(|r| {
            r.strip_prefix("refs/checkpoints/")
                .is_none_or(|id| !valid_hash(id))
        }) {
            return fail("unsafe_git", "unexpected private Git ref namespace");
        }
        Ok(())
    }
    fn cmd(&self) -> Command {
        let bin = if Path::new("/opt/homebrew/bin/git").is_file() {
            "/opt/homebrew/bin/git"
        } else {
            "/usr/bin/git"
        };
        let mut c = Command::new(bin);
        c.env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("LC_ALL", "C")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_ATTR_NOSYSTEM", "1")
            .env("GIT_AUTHOR_NAME", "forklift")
            .env("GIT_AUTHOR_EMAIL", "forklift@localhost")
            .env("GIT_COMMITTER_NAME", "forklift")
            .env("GIT_COMMITTER_EMAIL", "forklift@localhost")
            .arg("--no-replace-objects")
            .arg("--git-dir")
            .arg(self.root.join("repo"))
            .args([
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.attributesFile=/dev/null",
                "-c",
                "protocol.allow=never",
                "-c",
                "maintenance.auto=false",
            ]);
        c
    }
    fn run(&self, args: &[&str], input: &[u8]) -> Result<String> {
        let mut c = self.cmd();
        c.args(args);
        self.run_command(c, input)
    }
    fn run_command(&self, mut c: Command, input: &[u8]) -> Result<String> {
        let mut child = c
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        // Input can exceed a pipe buffer (large inventories); output is read concurrently.
        let out = std::thread::scope(|s| -> Result<_> {
            let mut stdin = child.stdin.take().unwrap();
            let writer = s.spawn(move || stdin.write_all(input));
            let out = child.wait_with_output()?;
            writer
                .join()
                .map_err(|_| crate::lifecycle::Error::new("git", "input writer panicked"))??;
            Ok(out)
        })?;
        if !out.status.success() {
            return fail("git", String::from_utf8_lossy(&out.stderr));
        }
        String::from_utf8(out.stdout)
            .map_err(|e| crate::lifecycle::Error::new("git", e.to_string()))
    }
    fn path(&self, id: &str) -> Result<PathBuf> {
        if !valid_hash(id) {
            return fail("invalid_manifest", "invalid manifest id");
        }
        Ok(self.root.join("manifests").join(format!("{id}.json")))
    }
    pub fn load(&self, id: &str, c: &Compatibility) -> Result<Manifest> {
        let bytes = objects::read_bounded(&self.path(id)?, objects::MANIFEST_LIMIT)?;
        if digest(&bytes) != id {
            return fail("integrity", "Git manifest checksum mismatch");
        }
        let m: Manifest = serde_json::from_slice(&bytes)?;
        m.validate(c)?;
        if m.format != 2 {
            return fail("invalid_manifest", "expected Git manifest");
        }
        // Verify the exact committed tree, including the manifest blob. Extra paths fail closed.
        let manifest_oid = self
            .run(&["hash-object", "--stdin", "--no-filters"], &bytes)?
            .trim()
            .to_owned();
        let committed_manifest = self.run(&["cat-file", "blob", &manifest_oid], b"")?;
        if committed_manifest.as_bytes() != bytes {
            return fail("integrity", "committed Git manifest differs");
        }
        let tree = self.run(
            &["ls-tree", "-r", "-z", &format!("refs/checkpoints/{id}")],
            b"",
        )?;
        let mut expected: Vec<String> = m
            .files
            .iter()
            .map(|f| {
                format!(
                    "100644 blob {}\tdata/{}",
                    f.git_blob.as_ref().unwrap(),
                    f.path
                )
            })
            .collect();
        expected.push(format!("100644 blob {manifest_oid}\tmanifest.json"));
        expected.sort();
        let mut actual: Vec<_> = tree
            .split('\0')
            .filter(|x| !x.is_empty())
            .map(str::to_owned)
            .collect();
        actual.sort();
        if actual != expected {
            return fail("integrity", "Git tree differs from manifest");
        }
        Ok(m)
    }
    pub fn capture(
        &self,
        data: &Path,
        compatibility: Compatibility,
        extensions: Vec<Extension>,
    ) -> Result<(String, CaptureStats)> {
        let _span = crate::timing::span("git_capture");
        let start = Instant::now();
        validate_tree(data)?;
        if data.join("postmaster.pid").exists() {
            return fail("not_stopped", "Git capture refuses a pidfile");
        }
        let mut m = Manifest {
            format: 2,
            chunk_bytes: 0,
            pack_bytes: 0,
            compatibility,
            extensions,
            directories: vec![],
            files: vec![],
            chunks: Default::default(),
        };
        let mut paths = vec![];
        objects::walk(data, data, &mut m.directories, &mut paths)?;
        paths.retain(|p| p != "postmaster.opts");
        paths.sort();
        m.directories.sort();
        let mut stats = CaptureStats::default();
        for path in &paths {
            let mut input = File::open(data.join(path))?;
            let length = input.metadata()?.len();
            let mut hash = Sha256::new();
            let mut buf = [0u8; 131072];
            loop {
                let n = input.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                hash.update(&buf[..n]);
            }
            stats.scanned_bytes += length;
            m.files.push(FileEntry {
                path: path.clone(),
                length,
                sha256: format!("{:x}", hash.finalize()),
                chunks: vec![],
                git_blob: None,
            });
        }
        let input = paths.iter().map(|p| format!("{p}\n")).collect::<String>();
        let mut cmd = self.cmd();
        cmd.current_dir(data)
            .args(["hash-object", "-w", "--no-filters", "--stdin-paths"]);
        let oids = self.run_command(cmd, input.as_bytes())?;
        let oids: Vec<_> = oids.lines().collect();
        if oids.len() != m.files.len() {
            return fail("git", "unexpected blob count");
        }
        for (f, oid) in m.files.iter_mut().zip(oids) {
            f.git_blob = Some(oid.into());
        }
        m.validate(&m.compatibility)?;
        let bytes = serde_json::to_vec(&m)?;
        if bytes.len() > objects::MANIFEST_LIMIT {
            return fail("limit", "manifest too large");
        }
        let id = digest(&bytes);
        let manifest_oid = self
            .run(&["hash-object", "-w", "--stdin", "--no-filters"], &bytes)?
            .trim()
            .to_owned();
        let index = self.root.join(format!(".index-{}", random_id()?));
        let mut entries = m
            .files
            .iter()
            .map(|f| format!("100644 {}\tdata/{}\0", f.git_blob.as_ref().unwrap(), f.path))
            .collect::<String>();
        entries.push_str(&format!("100644 {manifest_oid}\tmanifest.json\0"));
        let mut cmd = self.cmd();
        cmd.env("GIT_INDEX_FILE", &index)
            .args(["update-index", "-z", "--index-info"]);
        self.run_command(cmd, entries.as_bytes())?;
        let mut cmd = self.cmd();
        cmd.env("GIT_INDEX_FILE", &index).arg("write-tree");
        let tree = self.run_command(cmd, b"")?;
        fs::remove_file(index)?;
        let commit = self.run(&["commit-tree", tree.trim()], b"forklift checkpoint\n")?;
        // Persist objects and directory entries before publishing a ref or manifest/catalog.
        sync_tree(&self.root.join("repo/objects"))?;
        failpoint("git_objects_synced");
        self.run(
            &[
                "update-ref",
                &format!("refs/checkpoints/{id}"),
                commit.trim(),
            ],
            b"",
        )?;
        sync_tree(&self.root.join("repo/refs"))?;
        sync_dir(&self.root.join("repo"))?;
        failpoint("git_ref_published");
        // Do not bless corrupt reused Git blobs. Stream and verify every file before publication.
        self.read_files(&m, None)?;
        // Manifest identity uses the exact canonical serialized bytes.
        let dest = self.path(&id)?;
        let temp = dest.with_extension(format!("tmp-{}", random_id()?));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)?;
        file.write_all(&bytes)?;
        sync_file(&file)?;
        fs::rename(temp, dest)?;
        sync_dir(&self.root.join("manifests"))?;
        failpoint("git_manifest_published");
        stats.total_ms = start.elapsed().as_millis();
        Ok((id, stats))
    }
    fn read_files(&self, m: &Manifest, destination: Option<&Path>) -> Result<()> {
        let mut child = self
            .cmd()
            .args(["cat-file", "--batch"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let result = (|| -> Result<()> {
            let mut input = child.stdin.take().unwrap();
            let mut output = BufReader::new(child.stdout.take().unwrap());
            for f in &m.files {
                writeln!(input, "{}", f.git_blob.as_ref().unwrap())?;
                input.flush()?;
                let mut header = String::new();
                output.read_line(&mut header)?;
                if header.trim() != format!("{} blob {}", f.git_blob.as_ref().unwrap(), f.length) {
                    return fail("integrity", "missing/corrupt Git blob or wrong length/type");
                }
                let mut file = destination
                    .map(|d| {
                        OpenOptions::new()
                            .write(true)
                            .create_new(true)
                            .mode(0o600)
                            .open(d.join(&f.path))
                    })
                    .transpose()?;
                let mut remaining = f.length;
                let mut hash = Sha256::new();
                let mut buf = [0u8; 131072];
                while remaining > 0 {
                    let n = remaining.min(buf.len() as u64) as usize;
                    output.read_exact(&mut buf[..n])?;
                    hash.update(&buf[..n]);
                    if let Some(out) = &mut file {
                        out.write_all(&buf[..n])?;
                        failpoint("restore_during_write");
                    }
                    remaining -= n as u64;
                }
                let mut delimiter = [0];
                output.read_exact(&mut delimiter)?;
                if delimiter != [b'\n'] || format!("{:x}", hash.finalize()) != f.sha256 {
                    return fail("integrity", "Git file checksum mismatch");
                }
                if let Some(out) = file {
                    sync_file(&out)?;
                }
            }
            drop(input);
            Ok(())
        })();
        if result.is_err() {
            let _ = child.kill();
        }
        let status = child.wait()?;
        result?;
        if !status.success() {
            return fail("git", "Git blob reader failed");
        }
        Ok(())
    }
    pub fn materialize(&self, id: &str, c: &Compatibility, dest: &Path) -> Result<()> {
        let _span = crate::timing::span("git_materialize");
        let m = self.load(id, c)?;
        private_dir(dest)?;
        let mut dirs = m.directories.clone();
        dirs.sort();
        for d in &dirs {
            private_dir(&dest.join(d))?;
        }
        self.read_files(&m, Some(dest))?;
        for d in dirs.iter().rev() {
            sync_dir(&dest.join(d))?;
        }
        sync_dir(dest)?;
        sync_dir(dest.parent().unwrap())
    }
    pub fn export(&self, id: &str, c: &Compatibility, dest: &Path) -> Result<()> {
        let _span = crate::timing::span("git_export_conversion");
        let stage = self.root.join(format!(".export-{}", random_id()?));
        private_dir(&stage)?;
        let result = (|| {
            let m = self.load(id, c)?;
            self.materialize(id, c, &stage.join("data"))?;
            let repo = objects::Repository::open(&stage.join("objects"))?;
            let (portable, _) = repo.capture(&stage.join("data"), m.compatibility, m.extensions)?;
            repo.export(&portable, c, dest)
        })();
        fs::remove_dir_all(stage)?;
        result
    }
    pub fn gc(
        &self,
        roots: &BTreeSet<String>,
        c: &Compatibility,
        apply: bool,
    ) -> Result<serde_json::Value> {
        self.validate()?;
        for id in roots {
            self.load(id, c)?;
        }
        let refs = self.run(
            &["for-each-ref", "--format=%(refname)", "refs/checkpoints/"],
            b"",
        )?;
        let mut dead = vec![];
        for r in refs.lines() {
            let id = r.strip_prefix("refs/checkpoints/").unwrap();
            if !valid_hash(id) {
                return fail("unsafe_git", "unexpected snapshot ref");
            }
            if !roots.contains(id) {
                dead.push(id.to_owned());
            }
        }
        if apply {
            for id in &dead {
                let path = self.path(id)?;
                if path.exists() {
                    fs::remove_file(path)?;
                }
                sync_dir(&self.root.join("manifests"))?;
                self.run(
                    &["update-ref", "-d", &format!("refs/checkpoints/{id}")],
                    b"",
                )?;
                failpoint("git_gc_ref_removed");
            }
            sync_tree(&self.root.join("repo"))?;
            // Interrupted exports and temporary indexes are never catalog roots.
            for entry in fs::read_dir(&self.root)? {
                let p = entry?.path();
                let name = p.file_name().unwrap().to_string_lossy();
                if [".index-", ".export-"].iter().any(|prefix| {
                    name.strip_prefix(prefix).is_some_and(|id| {
                        id.len() == 16 && id.bytes().all(|b| b.is_ascii_hexdigit())
                    })
                }) {
                    crate::retention::safe_tree(&p)?;
                    if p.is_dir() {
                        fs::remove_dir_all(p)?;
                    } else {
                        fs::remove_file(p)?;
                    }
                }
            }
            sync_dir(&self.root)?;
            self.run(&["gc", "--prune=now"], b"")?;
            sync_tree(&self.root.join("repo"))?;
        }
        Ok(
            serde_json::json!({"backend":"git","applied":apply,"dead_snapshots":dead,"packing":apply,"note":"parentless commits permit forgetting; explicit gc packs all retained history and prunes unreachable objects"}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identical_files_deduplicate_and_git_tree_mapping_is_enforced() -> Result<()> {
        let root = std::env::temp_dir().join(format!("forklift-git-unit-{}", random_id()?));
        private_dir(&root)?;
        let data = root.join("data");
        private_dir(&data)?;
        for d in ["global", "pg_wal", "pg_tblspc", "empty"] {
            private_dir(&data.join(d))?;
        }
        fs::write(data.join("PG_VERSION"), b"17\n")?;
        fs::write(data.join("global/pg_control"), b"unit-control")?;
        fs::write(data.join("payload"), vec![7; 1024 * 1024 + 1])?;
        let c = Compatibility {
            postgres_major: 17,
            postgres_version: "unit".into(),
            platform: "unit".into(),
            build_configuration: "unit".into(),
            extension_installations: Default::default(),
        };
        let e = vec![Extension {
            database: "postgres".into(),
            name: "plpgsql".into(),
            version: "1.0".into(),
            installation_sha256: None,
        }];
        let repo = Repository::open(&root.join("git-store"))?;
        let (a, _) = repo.capture(&data, c.clone(), e.clone())?;
        let (b, _) = repo.capture(&data, c.clone(), e)?;
        assert_eq!(a, b);
        let m = repo.load(&a, &c)?;
        let mut bad = m.clone();
        bad.files[0].path = "../escape".into();
        assert_eq!(bad.validate(&c).unwrap_err().code, "invalid_manifest");
        repo.materialize(&a, &c, &root.join("restored"))?;
        objects::verify_materialized(&m, &root.join("restored"))?;
        assert!(root.join("restored/empty").is_dir());
        // A valid, self-hashed manifest cannot change the committed file mapping.
        let mut bad = m;
        bad.files[0].git_blob = Some("a".repeat(40));
        let bytes = serde_json::to_vec(&bad)?;
        let id = digest(&bytes);
        repo.run(&["hash-object", "-w", "--stdin", "--no-filters"], &bytes)?;
        fs::write(repo.path(&id)?, bytes)?;
        let commit = repo.run(&["rev-parse", &format!("refs/checkpoints/{a}")], b"")?;
        repo.run(
            &[
                "update-ref",
                &format!("refs/checkpoints/{id}"),
                commit.trim(),
            ],
            b"",
        )?;
        assert_eq!(repo.load(&id, &c).unwrap_err().code, "integrity");
        fs::remove_dir_all(root)?;
        Ok(())
    }
}
