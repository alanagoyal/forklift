//! Conservative filesystem checks shared by explicit retention operations.
use crate::lifecycle::{Result, fail};
use std::{
    fs,
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::Path,
};
pub(crate) fn safe_tree(path: &Path) -> Result<()> {
    let m = fs::symlink_metadata(path)?;
    if m.uid() != unsafe { libc::geteuid() }
        || m.permissions().mode() & 0o022 != 0
        || (!m.is_dir() && (!m.is_file() || m.nlink() != 1))
    {
        return fail(
            "unsafe_path",
            format!("unsafe retention path: {}", path.display()),
        );
    }
    if path.file_name().is_some_and(|n| n == "postmaster.pid") {
        return fail("not_stopped", "retention refuses a database pidfile");
    }
    if m.is_dir() {
        for e in fs::read_dir(path)? {
            safe_tree(&e?.path())?;
        }
    }
    Ok(())
}

pub(crate) fn safe_directory(path: &Path) -> Result<()> {
    let m = fs::symlink_metadata(path)?;
    if !m.is_dir() || m.uid() != unsafe { libc::geteuid() } || m.mode() & 0o022 != 0 {
        return fail(
            "unsafe_path",
            format!("unsafe retention directory: {}", path.display()),
        );
    }
    Ok(())
}
