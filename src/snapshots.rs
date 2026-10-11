//! Storage choice is attached to each checkpoint, not inferred from today's setting.
//! Lifecycle, compatibility, verified cache, and generation publication are shared.
use crate::{git_objects, lifecycle::Result, objects};
use objects::{CaptureStats, Compatibility, Extension, Manifest};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "kebab-case")]
pub enum Backend {
    #[default]
    Chunked,
    Git,
}
pub enum Repository {
    Chunked(objects::Repository),
    Git(git_objects::Repository),
}
impl Repository {
    pub fn open(store: &Path, backend: Backend) -> Result<Self> {
        Ok(match backend {
            Backend::Chunked => Self::Chunked(objects::Repository::open(&store.join("objects"))?),
            Backend::Git => Self::Git(git_objects::Repository::open(&store.join("git-store"))?),
        })
    }
    pub fn capture(
        &self,
        data: &Path,
        c: Compatibility,
        e: Vec<Extension>,
    ) -> Result<(String, CaptureStats)> {
        match self {
            Self::Chunked(r) => r.capture(data, c, e),
            Self::Git(r) => r.capture(data, c, e),
        }
    }
    pub fn load(&self, id: &str, c: &Compatibility) -> Result<Manifest> {
        match self {
            Self::Chunked(r) => r.load(id, c),
            Self::Git(r) => r.load(id, c),
        }
    }
    pub fn materialize(&self, id: &str, c: &Compatibility, dest: &Path) -> Result<()> {
        match self {
            Self::Chunked(r) => r.materialize(id, c, dest),
            Self::Git(r) => r.materialize(id, c, dest),
        }
    }
    pub fn export(&self, id: &str, c: &Compatibility, dest: &Path) -> Result<()> {
        match self {
            Self::Chunked(r) => r.export(id, c, dest),
            Self::Git(r) => r.export(id, c, dest),
        }
    }
    pub fn gc(
        &self,
        roots: &BTreeSet<String>,
        c: &Compatibility,
        apply: bool,
    ) -> Result<serde_json::Value> {
        match self {
            Self::Chunked(r) => r.gc(roots, c, apply),
            Self::Git(r) => r.gc(roots, c, apply),
        }
    }
}
