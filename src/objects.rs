//! Version-one immutable object repository. PostgreSQL still runs on materialized files.
use crate::{
    lifecycle::{Error, Result, fail, failpoint},
    storage::*,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    time::Instant,
};

pub const CHUNK: usize = 1024 * 1024;
pub const PACK: usize = 4 * CHUNK;
const MANIFEST_LIMIT: usize = 32 * 1024 * 1024;
const MAX_ENTRIES: usize = 100_000;
const MAX_BYTES: u64 = 1 << 40;
const MAGIC: &[u8; 8] = b"FLPK0001";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Compatibility {
    pub postgres_major: u32,
    pub postgres_version: String,
    pub platform: String,
    pub build_configuration: String,
    /// Local installation evidence; never trusted from a portable manifest.
    #[serde(skip)]
    pub extension_installations: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Extension {
    pub database: String,
    pub name: String,
    pub version: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub installation_sha256: Option<String>,
}
// These bundled modules require no third-party runtime library on the supported Mac.
pub const SUPPORTED_EXTENSIONS: &[(&str, &str)] = &[
    ("plpgsql", "1.0"),
    ("citext", "1.6"),
    ("hstore", "1.8"),
    ("uuid-ossp", "1.1"),
];

/// Hash the installed control file, SQL scripts and binary. Missing or nonstandard
/// installations are omitted, so an extension using one fails closed when needed.
pub fn extension_installations(sharedir: &Path, libdir: &Path) -> BTreeMap<String, String> {
    SUPPORTED_EXTENSIONS
        .iter()
        .filter(|(name, _)| *name != "plpgsql")
        .filter_map(|(name, _)| {
            installation_digest(sharedir, libdir, name)
                .ok()
                .map(|h| ((*name).into(), h))
        })
        .collect()
}
fn installation_digest(sharedir: &Path, libdir: &Path, name: &str) -> Result<String> {
    let library = libdir.join(format!("{name}.dylib"));
    let output = std::process::Command::new("/usr/bin/otool")
        .arg("-L")
        .arg(&library)
        .output()?;
    let dependencies = String::from_utf8_lossy(&output.stdout);
    if !output.status.success()
        || dependencies.lines().count() < 2
        || dependencies
            .lines()
            .skip(1)
            .any(|line| line.split_whitespace().next() != Some("/usr/lib/libSystem.B.dylib"))
    {
        return fail(
            "incompatible",
            "extension has unsupported runtime library dependencies",
        );
    }
    let dir = sharedir.join("extension");
    let mut files = vec![
        (format!("{name}.dylib"), library),
        (
            format!("{name}.control"),
            dir.join(format!("{name}.control")),
        ),
    ];
    for entry in fs::read_dir(&dir)? {
        let entry = entry?;
        let filename = entry.file_name().to_string_lossy().into_owned();
        if filename.starts_with(&format!("{name}--")) && filename.ends_with(".sql") {
            files.push((filename, entry.path()));
        }
    }
    if files.len() < 3 || files.len() > 100 {
        return fail("incompatible", "missing extension SQL scripts");
    }
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let mut hash = Sha256::new();
    for (name, path) in files {
        // Package managers commonly symlink these installation files. This is a
        // trusted local installation, unlike untrusted object repository paths.
        let mut file = File::open(path)?;
        if !file.metadata()?.is_file() || file.metadata()?.len() > 16 * 1024 * 1024 {
            return fail("incompatible", "invalid extension installation file");
        }
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take(16 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > 16 * 1024 * 1024 {
            return fail("incompatible", "oversized extension installation file");
        }
        hash.update(name.as_bytes());
        hash.update([0]);
        hash.update(digest(&bytes).as_bytes());
    }
    Ok(format!("{:x}", hash.finalize()))
}
pub fn validate_extensions(extensions: &[Extension], expected: &Compatibility) -> Result<()> {
    if extensions.is_empty() || extensions.len() > 1000 {
        return fail("incompatible", "missing or excessive extension metadata");
    }
    let mut seen = BTreeSet::new();
    for e in extensions {
        if e.database.is_empty()
            || e.database.len() > 63
            || e.database.contains('\0')
            || !seen.insert((&e.database, &e.name))
            || !SUPPORTED_EXTENSIONS.contains(&(e.name.as_str(), e.version.as_str()))
        {
            return fail(
                "incompatible",
                "unsupported extension/version or invalid database metadata; supported: plpgsql 1.0, citext 1.6, hstore 1.8, uuid-ossp 1.1",
            );
        }
        if e.name == "plpgsql" {
            if e.installation_sha256.is_some() {
                return fail("incompatible", "unexpected plpgsql installation metadata");
            }
        } else if e.installation_sha256.as_ref().is_none_or(|h| {
            !valid_hash(h) || expected.extension_installations.get(&e.name) != Some(h)
        }) {
            return fail(
                "incompatible",
                format!(
                    "{} requires matching installed control, SQL and library files with supported dependencies",
                    e.name
                ),
            );
        }
    }
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChunkRef {
    pub pack: String,
    pub offset: u64,
    pub length: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileEntry {
    pub path: String,
    pub length: u64,
    pub sha256: String,
    pub chunks: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub format: u32,
    pub chunk_bytes: usize,
    pub pack_bytes: usize,
    pub compatibility: Compatibility,
    pub extensions: Vec<Extension>,
    pub directories: Vec<String>,
    pub files: Vec<FileEntry>,
    pub chunks: BTreeMap<String, ChunkRef>,
}
#[derive(Default, Serialize)]
pub struct CaptureStats {
    pub scanned_bytes: u64,
    pub chunk_references: u64,
    pub reused_existing_references: u64,
    pub reused_existing_bytes: u64,
    pub reused_in_capture_references: u64,
    pub new_packs: u64,
    pub new_pack_bytes: u64,
    pub hash_us: u128,
    pub compression_us: u128,
    pub write_sync_us: u128,
    pub reuse_validation_us: u128,
    pub total_ms: u128,
}

pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn valid_hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn timed_hash(bytes: &[u8], stats: &mut CaptureStats) -> String {
    let t = Instant::now();
    let h = digest(bytes);
    stats.hash_us += t.elapsed().as_micros();
    h
}
fn safe_path(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 512
        && s.split('/').count() <= 16
        && s.split('/').all(|p| {
            !p.is_empty()
                && p != "."
                && p != ".."
                && p.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-' || b == b'.')
        })
}
fn read_bounded(path: &Path, max: usize) -> Result<Vec<u8>> {
    let mut f = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                Error::new("missing_object", format!("missing {}", path.display()))
            } else {
                e.into()
            }
        })?;
    let m = f.metadata()?;
    if !m.is_file() || m.nlink() != 1 {
        return fail("unsafe_path", "object must be a regular, non-linked file");
    }
    if m.len() > max as u64 {
        return fail("integrity", "object exceeds format size limit");
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut f)
        .take(max as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > max {
        return fail("integrity", "object grew beyond size limit");
    }
    Ok(bytes)
}
fn write_synced(path: &Path, bytes: &[u8], interrupt: bool) -> Result<()> {
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    if interrupt {
        let split = bytes.len() / 2;
        f.write_all(&bytes[..split])?;
        f.sync_all()?;
        failpoint("object_during_write");
        f.write_all(&bytes[split..])?;
    } else {
        f.write_all(bytes)?;
    }
    f.sync_all()?;
    Ok(())
}
impl Manifest {
    pub fn validate(&self, expected: &Compatibility) -> Result<()> {
        if self.format != 1
            || self.chunk_bytes != CHUNK
            || self.pack_bytes != PACK
            || self.compatibility.postgres_major != expected.postgres_major
            || self.compatibility.postgres_version != expected.postgres_version
            || self.compatibility.platform != expected.platform
            || self.compatibility.build_configuration != expected.build_configuration
        {
            return fail(
                "incompatible",
                "checkpoint format, platform, PostgreSQL version or build configuration differs",
            );
        }
        validate_extensions(&self.extensions, expected)?;
        if self.files.len() + self.directories.len() > MAX_ENTRIES
            || self.chunks.len() > MAX_ENTRIES
        {
            return fail("invalid_manifest", "manifest entry limit exceeded");
        }
        let mut paths = BTreeSet::new();
        let mut dirs = BTreeSet::new();
        for p in &self.directories {
            if !safe_path(p) || !paths.insert(p.to_ascii_lowercase()) {
                return fail("invalid_manifest", "unsafe or duplicate directory path");
            }
            dirs.insert(p.as_str());
        }
        let mut total = 0u64;
        let mut reachable = BTreeSet::new();
        for f in &self.files {
            if !safe_path(&f.path)
                || !paths.insert(f.path.to_ascii_lowercase())
                || !valid_hash(&f.sha256)
                || [
                    "postmaster.pid",
                    "postmaster.opts",
                    "standby.signal",
                    "recovery.signal",
                ]
                .contains(&f.path.as_str())
            {
                return fail(
                    "invalid_manifest",
                    "unsafe, duplicate or forbidden file path",
                );
            }
            if f.length > MAX_BYTES || f.chunks.len() > MAX_ENTRIES {
                return fail("invalid_manifest", "file size/chunk count limit exceeded");
            }
            total = total
                .checked_add(f.length)
                .ok_or_else(|| Error::new("invalid_manifest", "length overflow"))?;
            let mut length = 0u64;
            for (n, h) in f.chunks.iter().enumerate() {
                let c = self
                    .chunks
                    .get(h)
                    .ok_or_else(|| Error::new("invalid_manifest", "missing chunk reference"))?;
                if !valid_hash(h)
                    || !valid_hash(&c.pack)
                    || c.length == 0
                    || c.length > CHUNK as u64
                    || c.offset
                        .checked_add(c.length)
                        .is_none_or(|end| end > PACK as u64)
                    || (n + 1 < f.chunks.len() && c.length != CHUNK as u64)
                {
                    return fail("invalid_manifest", "invalid chunk length, hash or offset");
                }
                length = length
                    .checked_add(c.length)
                    .ok_or_else(|| Error::new("invalid_manifest", "length overflow"))?;
                reachable.insert(h);
            }
            if length != f.length {
                return fail("invalid_manifest", "file length does not match its chunks");
            }
        }
        if total > MAX_BYTES || reachable.len() != self.chunks.len() {
            return fail(
                "invalid_manifest",
                "total size exceeded or unreferenced chunks present",
            );
        }
        for p in self
            .directories
            .iter()
            .chain(self.files.iter().map(|f| &f.path))
        {
            if let Some((parent, _)) = p.rsplit_once('/')
                && !dirs.contains(parent)
            {
                return fail(
                    "invalid_manifest",
                    "missing parent directory or file/directory conflict",
                );
            }
        }
        if !self.files.iter().any(|f| f.path == "PG_VERSION")
            || !self.files.iter().any(|f| f.path == "global/pg_control")
            || !dirs.contains("pg_wal")
            || !dirs.contains("pg_tblspc")
        {
            return fail(
                "invalid_manifest",
                "required PostgreSQL files/directories are missing",
            );
        }
        if self.files.iter().any(|f| f.path.starts_with("pg_tblspc/"))
            || self.directories.iter().any(|d| d.starts_with("pg_tblspc/"))
        {
            return fail("incompatible", "external tablespaces are unsupported");
        }
        Ok(())
    }
    fn pack_ids(&self) -> BTreeSet<&str> {
        self.chunks.values().map(|c| c.pack.as_str()).collect()
    }
}

pub struct Repository {
    root: PathBuf,
}
impl Repository {
    pub fn open(root: &Path) -> Result<Self> {
        if !root.exists() {
            private_dir(root)?;
            sync_dir(root.parent().unwrap())?;
        }
        directory(root)?;
        for sub in ["packs", "manifests"] {
            let p = root.join(sub);
            if !p.exists() {
                private_dir(&p)?;
                sync_dir(root)?;
            }
            directory(&p)?;
        }
        Ok(Self {
            root: root.to_owned(),
        })
    }
    fn packs(&self) -> PathBuf {
        self.root.join("packs")
    }
    fn manifest_path(&self, id: &str) -> Result<PathBuf> {
        if !valid_hash(id) {
            return fail("invalid_manifest", "invalid manifest identifier");
        }
        Ok(self.root.join("manifests").join(format!("{id}.json")))
    }
    pub fn load(&self, id: &str, expected: &Compatibility) -> Result<Manifest> {
        load_manifest(&self.manifest_path(id)?, id, expected)
    }
    fn existing_chunks(&self, expected: &Compatibility) -> Result<BTreeMap<String, ChunkRef>> {
        let mut index = BTreeMap::new();
        for entry in fs::read_dir(self.root.join("manifests"))? {
            let p = entry?.path();
            let name = p.file_name().unwrap().to_string_lossy();
            if let Some(id) = name.strip_suffix(".json")
                && valid_hash(id)
            {
                for (hash, c) in self.load(id, expected)?.chunks {
                    index.entry(hash).or_insert(c);
                }
            }
        }
        Ok(index)
    }
    fn flush(
        &self,
        buffer: &mut Vec<u8>,
        pending: &mut Vec<(String, u64, u64)>,
        index: &mut BTreeMap<String, ChunkRef>,
        stats: &mut CaptureStats,
    ) -> Result<()> {
        if buffer.is_empty() {
            return Ok(());
        }
        let id = timed_hash(buffer, stats);
        let path = self.packs().join(format!("{id}.pack"));
        if path.exists() {
            let raw = read_pack(&self.packs(), &id)?;
            if &raw != buffer {
                return fail(
                    "integrity",
                    "existing pack differs from its expected content",
                );
            }
        } else {
            let t = Instant::now();
            let compressed = zstd::stream::encode_all(buffer.as_slice(), 3)?;
            stats.compression_us += t.elapsed().as_micros();
            let mut bytes = Vec::with_capacity(16 + compressed.len());
            bytes.extend_from_slice(MAGIC);
            bytes.extend_from_slice(&(buffer.len() as u64).to_le_bytes());
            bytes.extend_from_slice(&compressed);
            let temp = self.packs().join(format!(".tmp-{}", random_id()?));
            let t = Instant::now();
            write_synced(&temp, &bytes, true)?;
            publish_new(&temp, &path)?;
            stats.write_sync_us += t.elapsed().as_micros();
            stats.new_packs += 1;
            stats.new_pack_bytes += bytes.len() as u64;
            failpoint("object_written");
        }
        for (hash, offset, length) in pending.drain(..) {
            index.insert(
                hash,
                ChunkRef {
                    pack: id.clone(),
                    offset,
                    length,
                },
            );
        }
        buffer.clear();
        Ok(())
    }
    pub fn capture(
        &self,
        data: &Path,
        compatibility: Compatibility,
        extensions: Vec<Extension>,
    ) -> Result<(String, CaptureStats)> {
        let total = Instant::now();
        validate_tree(data)?;
        let mut index = self.existing_chunks(&compatibility)?;
        let previous: BTreeSet<_> = index.keys().cloned().collect();
        let mut m = Manifest {
            format: 1,
            chunk_bytes: CHUNK,
            pack_bytes: PACK,
            compatibility,
            extensions,
            directories: vec![],
            files: vec![],
            chunks: BTreeMap::new(),
        };
        let mut paths = Vec::new();
        walk(data, data, &mut m.directories, &mut paths)?;
        paths.sort();
        m.directories.sort();
        let mut stats = CaptureStats::default();
        let mut buffer = Vec::with_capacity(PACK);
        let mut pending = vec![];
        let mut cache = PackCache::default();
        for path in paths {
            if path == "postmaster.opts" {
                continue;
            }
            let mut input = File::open(data.join(&path))?;
            let mut file_hash = Sha256::new();
            let mut chunks = vec![];
            let mut length = 0u64;
            loop {
                let mut chunk = vec![0u8; CHUNK];
                let mut n = 0;
                while n < CHUNK {
                    let read = input.read(&mut chunk[n..])?;
                    if read == 0 {
                        break;
                    }
                    n += read;
                }
                if n == 0 {
                    break;
                }
                chunk.truncate(n);
                length += n as u64;
                stats.scanned_bytes += n as u64;
                stats.chunk_references += 1;
                let t = Instant::now();
                file_hash.update(&chunk);
                stats.hash_us += t.elapsed().as_micros();
                let hash = timed_hash(&chunk, &mut stats);
                if let Some(c) = index.get(&hash) {
                    if previous.contains(&hash) {
                        let t = Instant::now();
                        let bytes = cache.chunk(&self.packs(), &hash, c)?;
                        if bytes != chunk {
                            return fail("integrity", "existing chunk content mismatch");
                        }
                        stats.reuse_validation_us += t.elapsed().as_micros();
                        stats.reused_existing_references += 1;
                        stats.reused_existing_bytes += n as u64;
                    } else {
                        stats.reused_in_capture_references += 1;
                    }
                } else {
                    if buffer.len() + n > PACK {
                        self.flush(&mut buffer, &mut pending, &mut index, &mut stats)?;
                    }
                    let offset = buffer.len() as u64;
                    buffer.extend_from_slice(&chunk);
                    pending.push((hash.clone(), offset, n as u64));
                    index.insert(
                        hash.clone(),
                        ChunkRef {
                            pack: String::new(),
                            offset,
                            length: n as u64,
                        },
                    );
                }
                chunks.push(hash);
                if stats.scanned_bytes > MAX_BYTES || index.len() > MAX_ENTRIES {
                    return fail("limit", "checkpoint exceeds format limits");
                }
            }
            let t = Instant::now();
            let sha256 = format!("{:x}", file_hash.finalize());
            stats.hash_us += t.elapsed().as_micros();
            m.files.push(FileEntry {
                path,
                length,
                sha256,
                chunks,
            });
        }
        self.flush(&mut buffer, &mut pending, &mut index, &mut stats)?;
        for hash in m.files.iter().flat_map(|f| &f.chunks) {
            m.chunks.insert(hash.clone(), index[hash].clone());
        }
        m.validate(&m.compatibility)?;
        let bytes = serde_json::to_vec(&m)?;
        if bytes.len() > MANIFEST_LIMIT {
            return fail("limit", "manifest exceeds 32 MiB");
        }
        let id = timed_hash(&bytes, &mut stats);
        failpoint("manifest_before_publish");
        let dest = self.manifest_path(&id)?;
        if dest.exists() {
            self.load(&id, &m.compatibility)?;
        } else {
            let temp = self
                .root
                .join("manifests")
                .join(format!(".tmp-{}", random_id()?));
            let t = Instant::now();
            write_synced(&temp, &bytes, false)?;
            publish_new(&temp, &dest)?;
            stats.write_sync_us += t.elapsed().as_micros();
        }
        failpoint("manifest_published");
        stats.total_ms = total.elapsed().as_millis();
        Ok((id, stats))
    }
    pub fn materialize(
        &self,
        id: &str,
        expected: &Compatibility,
        destination: &Path,
    ) -> Result<()> {
        let m = self.load(id, expected)?;
        materialize(&m, &self.packs(), destination)
    }
    pub fn export(&self, id: &str, expected: &Compatibility, output: &Path) -> Result<()> {
        let m = self.load(id, expected)?;
        if fs::symlink_metadata(output).is_ok() {
            return fail("already_exists", "export destination already exists");
        }
        let parent = output
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        directory(parent)?;
        let stage = parent.join(format!(".forklift-export-{}", random_id()?));
        private_dir(&stage)?;
        private_dir(&stage.join("packs"))?;
        for pack in m.pack_ids() {
            read_pack(&self.packs(), pack)?;
            let name = format!("{pack}.pack");
            let bytes = read_bounded(&self.packs().join(&name), PACK + 131072)?;
            write_synced(&stage.join("packs").join(name), &bytes, false)?;
            failpoint("export_pack_written");
        }
        sync_dir(&stage.join("packs"))?;
        write_synced(
            &stage.join("manifest.json"),
            &read_bounded(&self.manifest_path(id)?, MANIFEST_LIMIT)?,
            false,
        )?;
        write_synced(&stage.join("manifest.sha256"), id.as_bytes(), false)?;
        sync_dir(&stage)?;
        failpoint("export_before_publish");
        publish_new(&stage, output)
    }
}
fn walk(root: &Path, at: &Path, dirs: &mut Vec<String>, files: &mut Vec<String>) -> Result<()> {
    for e in fs::read_dir(at)? {
        let p = e?.path();
        let rel = p
            .strip_prefix(root)
            .unwrap()
            .to_str()
            .ok_or_else(|| Error::new("unsafe_path", "non-UTF8 data path"))?
            .to_owned();
        if !safe_path(&rel) {
            return fail("unsafe_path", "unsupported data path");
        }
        if p.is_dir() {
            dirs.push(rel);
            walk(root, &p, dirs, files)?;
        } else {
            files.push(rel);
        }
        if dirs.len() + files.len() > MAX_ENTRIES {
            return fail("limit", "too many files");
        }
    }
    Ok(())
}
fn load_manifest(path: &Path, id: &str, expected: &Compatibility) -> Result<Manifest> {
    if !valid_hash(id) {
        return fail("invalid_manifest", "invalid manifest hash");
    }
    let bytes = read_bounded(path, MANIFEST_LIMIT)?;
    if digest(&bytes) != id {
        return fail("integrity", "manifest checksum mismatch");
    }
    let m: Manifest = serde_json::from_slice(&bytes)
        .map_err(|e| Error::new("invalid_manifest", e.to_string()))?;
    m.validate(expected)?;
    Ok(m)
}
fn read_pack(packs: &Path, id: &str) -> Result<Vec<u8>> {
    if !valid_hash(id) {
        return fail("invalid_manifest", "invalid pack hash");
    }
    directory(packs)?;
    let bytes = read_bounded(&packs.join(format!("{id}.pack")), PACK + 131072)?;
    if bytes.len() < 16 || &bytes[..8] != MAGIC {
        return fail("integrity", "invalid pack header");
    }
    let length = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
    if length == 0 || length > PACK as u64 {
        return fail("integrity", "invalid pack length");
    }
    let decoder = zstd::stream::read::Decoder::new(&bytes[16..])
        .map_err(|e| Error::new("integrity", e.to_string()))?;
    let mut raw = Vec::new();
    decoder
        .take(length + 1)
        .read_to_end(&mut raw)
        .map_err(|e| Error::new("integrity", e.to_string()))?;
    if raw.len() != length as usize || digest(&raw) != id {
        return fail("integrity", "pack checksum or length mismatch");
    }
    Ok(raw)
}
// Four packs bound decompressed cache memory to 16 MiB and avoid repeated
// decompression when a changed checkpoint alternates old and new packs.
#[derive(Default)]
struct PackCache {
    packs: BTreeMap<String, Vec<u8>>,
    order: VecDeque<String>,
}
impl PackCache {
    fn chunk(&mut self, packs: &Path, hash: &str, c: &ChunkRef) -> Result<&[u8]> {
        if !self.packs.contains_key(&c.pack) {
            let bytes = read_pack(packs, &c.pack)?;
            if self.packs.len() == 4 {
                let old = self.order.pop_front().unwrap();
                self.packs.remove(&old);
            }
            self.packs.insert(c.pack.clone(), bytes);
        }
        if let Some(position) = self.order.iter().position(|id| id == &c.pack) {
            self.order.remove(position);
        }
        self.order.push_back(c.pack.clone());
        let raw = &self.packs[&c.pack];
        let end = c
            .offset
            .checked_add(c.length)
            .ok_or_else(|| Error::new("integrity", "chunk offset overflow"))?;
        if end > raw.len() as u64 {
            return fail("integrity", "chunk exceeds pack");
        }
        let bytes = &raw[c.offset as usize..end as usize];
        if digest(bytes) != hash {
            return fail("integrity", "chunk checksum mismatch");
        }
        Ok(bytes)
    }
}

fn materialize(m: &Manifest, packs: &Path, destination: &Path) -> Result<()> {
    private_dir(destination)?;
    let mut dirs = m.directories.clone();
    dirs.sort_by_key(|p| p.split('/').count());
    for d in &dirs {
        private_dir(&destination.join(d))?;
    }
    let mut cache = PackCache::default();
    for f in &m.files {
        let mut out = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(destination.join(&f.path))?;
        let mut hash = Sha256::new();
        for h in &f.chunks {
            let chunk = cache.chunk(packs, h, &m.chunks[h])?;
            out.write_all(chunk)?;
            hash.update(chunk);
            failpoint("restore_during_write");
        }
        if format!("{:x}", hash.finalize()) != f.sha256 {
            return fail("integrity", "file checksum mismatch");
        }
        out.sync_all()?;
    }
    for d in dirs.iter().rev() {
        sync_dir(&destination.join(d))?;
    }
    sync_dir(destination)?;
    sync_dir(destination.parent().unwrap())?;
    Ok(())
}
/// Verify an exact materialization without trusting filesystem timestamps. Caches
/// are private and never started by Forklift; extra files are also an error.
pub fn verify_materialized(manifest: &Manifest, data: &Path) -> Result<()> {
    validate_tree(data)?;
    let mut dirs = vec![];
    let mut files = vec![];
    walk(data, data, &mut dirs, &mut files)?;
    dirs.sort();
    files.sort();
    let mut expected_dirs = manifest.directories.clone();
    expected_dirs.sort();
    let mut expected_files: Vec<_> = manifest.files.iter().map(|f| f.path.clone()).collect();
    expected_files.sort();
    if dirs != expected_dirs || files != expected_files {
        return fail("cache_integrity", "cache paths differ from checkpoint");
    }
    for entry in &manifest.files {
        let mut file = File::open(data.join(&entry.path))?;
        if file.metadata()?.len() != entry.length {
            return fail(
                "cache_integrity",
                "cache file length differs from checkpoint",
            );
        }
        let mut hasher = Sha256::new();
        let mut buf = [0u8; 131072];
        loop {
            let n = file.read(&mut buf)?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
        }
        if format!("{:x}", hasher.finalize()) != entry.sha256 {
            return fail(
                "cache_integrity",
                format!("cache checksum mismatch: {}", entry.path),
            );
        }
    }
    Ok(())
}

pub fn restore_bundle(
    bundle: &Path,
    expected: &Compatibility,
    destination: &Path,
) -> Result<String> {
    directory(bundle)?;
    directory(&bundle.join("packs"))?;
    let id = String::from_utf8(read_bounded(&bundle.join("manifest.sha256"), 64)?)
        .map_err(|_| Error::new("invalid_manifest", "invalid manifest identifier"))?;
    let m = load_manifest(&bundle.join("manifest.json"), &id, expected)?;
    materialize(&m, &bundle.join("packs"), destination)?;
    Ok(id)
}
pub fn validate_bundle(bundle: &Path, expected: &Compatibility) -> Result<String> {
    directory(bundle)?;
    directory(&bundle.join("packs"))?;
    let id = String::from_utf8(read_bounded(&bundle.join("manifest.sha256"), 64)?)
        .map_err(|_| Error::new("invalid_manifest", "invalid manifest identifier"))?;
    load_manifest(&bundle.join("manifest.json"), &id, expected)?;
    Ok(id)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn compatibility() -> Compatibility {
        Compatibility {
            postgres_major: 17,
            postgres_version: "unit fixture".into(),
            platform: "unit".into(),
            build_configuration: "unit".into(),
            extension_installations: BTreeMap::new(),
        }
    }
    fn root() -> PathBuf {
        let p = std::env::temp_dir().join(format!("forklift-objects-{}", random_id().unwrap()));
        private_dir(&p).unwrap();
        p
    }
    #[test]
    fn exact_repeat_reuses_all_content_and_restore_is_independent() -> Result<()> {
        let root = root();
        let data = root.join("source");
        private_dir(&data)?;
        for d in ["global", "pg_wal", "pg_tblspc", "empty"] {
            private_dir(&data.join(d))?;
        }
        fs::write(data.join("PG_VERSION"), b"17\n")?;
        fs::write(data.join("global/pg_control"), b"unit-control")?;
        fs::write(data.join("payload"), vec![7u8; CHUNK * 2 + 13])?;
        let repo = Repository::open(&root.join("objects"))?;
        let ext = vec![Extension {
            database: "postgres".into(),
            name: "plpgsql".into(),
            version: "1.0".into(),
            installation_sha256: None,
        }];
        let (first, a) = repo.capture(&data, compatibility(), ext.clone())?;
        let (second, b) = repo.capture(&data, compatibility(), ext)?;
        assert_eq!(first, second);
        assert!(a.new_packs > 0);
        assert_eq!(b.new_packs, 0);
        assert_eq!(b.reused_existing_bytes, b.scanned_bytes);
        fs::remove_dir_all(&data)?;
        repo.materialize(&first, &compatibility(), &root.join("restored"))?;
        assert_eq!(
            fs::read(root.join("restored/payload"))?,
            vec![7u8; CHUNK * 2 + 13]
        );
        assert!(root.join("restored/empty").is_dir());
        let mut m = repo.load(&first, &compatibility())?;
        m.files[0].path = "../escaped".into();
        assert_eq!(
            m.validate(&compatibility()).unwrap_err().code,
            "invalid_manifest"
        );
        let mut m = repo.load(&first, &compatibility())?;
        let mut duplicate = m.files[0].clone();
        duplicate.path = duplicate.path.to_ascii_lowercase();
        m.files.push(duplicate);
        assert!(m.validate(&compatibility()).is_err());
        let mut m = repo.load(&first, &compatibility())?;
        m.chunks.values_mut().next().unwrap().offset = u64::MAX;
        assert!(m.validate(&compatibility()).is_err());
        fs::remove_dir_all(root)?;
        Ok(())
    }
    #[test]
    fn extension_metadata_is_explicit_and_fails_closed() {
        let old: Extension =
            serde_json::from_str(r#"{"database":"postgres","name":"plpgsql","version":"1.0"}"#)
                .unwrap();
        let mut expected = compatibility();
        assert!(validate_extensions(std::slice::from_ref(&old), &expected).is_ok());
        assert!(validate_extensions(&[old.clone(), old.clone()], &expected).is_err());
        let hash = "a".repeat(64);
        expected
            .extension_installations
            .insert("citext".into(), hash.clone());
        let supported = Extension {
            database: "postgres".into(),
            name: "citext".into(),
            version: "1.6".into(),
            installation_sha256: Some(hash),
        };
        assert!(validate_extensions(std::slice::from_ref(&supported), &expected).is_ok());
        let mut malformed = supported.clone();
        malformed.installation_sha256 = None;
        assert!(validate_extensions(&[malformed], &expected).is_err());
        let mut malformed = supported.clone();
        malformed.installation_sha256 = Some("../bad".into());
        assert!(validate_extensions(&[malformed], &expected).is_err());
        let mut malformed = supported.clone();
        malformed.version = "1.5".into();
        assert!(validate_extensions(&[malformed], &expected).is_err());
        let mut malformed = supported.clone();
        malformed.database = "bad\0name".into();
        assert!(validate_extensions(&[malformed], &expected).is_err());
        let mut malformed = supported.clone();
        malformed.name = "pgcrypto".into();
        assert!(validate_extensions(&[malformed], &expected).is_err());
        let serialized = serde_json::to_vec(&expected).unwrap();
        let destination: Compatibility = serde_json::from_slice(&serialized).unwrap();
        assert!(destination.extension_installations.is_empty());
        assert!(validate_extensions(&[supported], &destination).is_err());
        assert!(serde_json::from_str::<Compatibility>(r#"{"postgres_major":17,"postgres_version":"x","platform":"x","build_configuration":"x","extension_installations":{}}"#).is_err());
    }
    #[test]
    fn bounded_decoder_and_exclusive_publication() -> Result<()> {
        let root = root();
        let packs = root.join("packs");
        private_dir(&packs)?;
        let data = vec![0u8; CHUNK * 2];
        let id = digest(&data);
        let mut bytes = MAGIC.to_vec();
        bytes.extend_from_slice(&1u64.to_le_bytes());
        bytes.extend(zstd::stream::encode_all(data.as_slice(), 3)?);
        fs::write(packs.join(format!("{id}.pack")), bytes)?;
        assert_eq!(read_pack(&packs, &id).unwrap_err().code, "integrity");
        let a = root.join("a");
        let b = root.join("b");
        private_dir(&a)?;
        private_dir(&b)?;
        fs::write(b.join("sentinel"), b"untouched")?;
        assert!(publish_new(&a, &b).is_err());
        assert_eq!(fs::read(b.join("sentinel"))?, b"untouched");
        fs::remove_dir_all(root)?;
        Ok(())
    }
}
