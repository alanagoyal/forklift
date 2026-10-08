//! Disposable materializations of immutable checkpoints. No cache is a runnable branch.
use crate::{
    lifecycle::{Result, fail, failpoint},
    objects::{self, Compatibility, Repository},
    storage::*,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Instant,
};
#[derive(Serialize, Deserialize)]
struct Marker {
    format: u32,
    store_id: String,
    manifest: String,
}

pub fn prepare(
    root: &Path,
    store_id: &str,
    id: &str,
    repo: &Repository,
    compatibility: &Compatibility,
    events: &mut Vec<Value>,
    check_stopped: impl Fn(&Path) -> Result<()>,
) -> Result<PathBuf> {
    let manifest = repo.load(id, compatibility)?;
    if !root.exists() {
        private_dir(root)?;
        sync_dir(root.parent().unwrap())?;
    }
    directory(root)?;
    let published = root.join(id);
    if fs::symlink_metadata(&published).is_ok() {
        directory(&published)?;
        regular(&published.join("entry.json"))?;
        if fs::metadata(published.join("entry.json"))?.len() > 4096 {
            return fail("cache_identity", "invalid cache marker size");
        }
        let marker: Marker = serde_json::from_slice(&fs::read(published.join("entry.json"))?)?;
        if marker.format != 1 || marker.store_id != store_id || marker.manifest != id {
            return fail("cache_identity", "cache ownership/manifest does not match");
        }
        let data = published.join("data");
        directory(&data)?;
        if fs::symlink_metadata(data.join("postmaster.pid")).is_ok() {
            return fail(
                "cache_running",
                "cache has a pidfile; refusing to copy, move or signal it",
            );
        }
        let t = Instant::now();
        // Unsafe links/paths fail closed. Ordinary content corruption is quarantined
        // and rebuilt from the immutable objects; it is never copied into a branch.
        validate_tree(&data)?;
        match objects::verify_materialized(&manifest, &data) {
            Ok(()) => {
                check_stopped(&data)?;
                events.push(json!({"stage":"cache_hit_verify","elapsed_ms":t.elapsed().as_millis(),"manifest":id}));
                return Ok(data);
            }
            Err(e) if e.code == "cache_integrity" => {
                let quarantine = root.join(format!(".invalid-{id}-{}", random_id()?));
                publish_new(&published, &quarantine)?;
                events.push(json!({"stage":"cache_quarantined","reason":e.message,"manifest":id}));
            }
            Err(e) => return Err(e),
        }
    }
    let t = Instant::now();
    let stage = root.join(format!(".building-{id}-{}", random_id()?));
    private_dir(&stage)?;
    repo.materialize(id, compatibility, &stage.join("data"))?;
    check_stopped(&stage.join("data"))?;
    atomic_json(
        &stage.join("entry.json"),
        &Marker {
            format: 1,
            store_id: store_id.into(),
            manifest: id.into(),
        },
    )?;
    sync_dir(&stage)?;
    failpoint("cache_before_publish");
    publish_new(&stage, &published)?;
    failpoint("cache_published");
    events.push(json!({"stage":"cache_build_verify_publish","elapsed_ms":t.elapsed().as_millis(),"manifest":id}));
    Ok(published.join("data"))
}
