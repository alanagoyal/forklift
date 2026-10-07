use crate::lifecycle::{Result, fail};
use std::{
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    },
    path::Path,
};

pub fn private_dir(path: &Path) -> Result<()> {
    fs::create_dir(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}
pub fn regular(path: &Path) -> Result<()> {
    if !fs::symlink_metadata(path)?.is_file() {
        return fail(
            "unsafe_path",
            format!("not a regular file: {}", path.display()),
        );
    }
    Ok(())
}
pub fn directory(path: &Path) -> Result<()> {
    if !fs::symlink_metadata(path)?.is_dir() {
        return fail(
            "unsafe_path",
            format!("not a directory: {}", path.display()),
        );
    }
    Ok(())
}
pub fn sync_dir(path: &Path) -> Result<()> {
    File::open(path)?.sync_all()?;
    Ok(())
}
pub fn atomic_json<T: serde::Serialize>(path: &Path, value: &T) -> Result<()> {
    let tmp = path.with_extension(format!("tmp-{}", random_id()?));
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)?;
    f.write_all(&serde_json::to_vec_pretty(value)?)?;
    f.write_all(b"\n")?;
    f.sync_all()?;
    fs::rename(&tmp, path)?;
    sync_dir(path.parent().unwrap())
}
pub fn random_id() -> Result<String> {
    let mut bytes = [0u8; 8];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}
pub fn validate_tree(path: &Path) -> Result<()> {
    directory(path)?;
    for e in fs::read_dir(path)? {
        let p = e?.path();
        let m = fs::symlink_metadata(&p)?;
        if m.is_dir() {
            validate_tree(&p)?;
        } else if !m.is_file() || m.nlink() != 1 {
            return fail(
                "unsafe_path",
                format!("links/special files are unsupported: {}", p.display()),
            );
        }
    }
    Ok(())
}
#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn clonefile(src: *const std::ffi::c_char, dst: *const std::ffi::c_char, flags: u32) -> i32;
}
fn clone_one(src: &Path, dst: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let a = CString::new(src.as_os_str().as_bytes())
            .map_err(|e| crate::lifecycle::Error::new("unsafe_path", e.to_string()))?;
        let b = CString::new(dst.as_os_str().as_bytes())
            .map_err(|e| crate::lifecycle::Error::new("unsafe_path", e.to_string()))?;
        // Both C strings live for the call; errors are propagated without a copy fallback.
        if unsafe { clonefile(a.as_ptr(), b.as_ptr(), 0) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (src, dst);
        fail(
            "unsupported",
            "APFS cloning requires macOS; use --copy-mode full explicitly",
        )
    }
}
pub fn copy_tree(src: &Path, dst: &Path, full: bool) -> Result<()> {
    directory(src)?;
    private_dir(dst)?;
    for e in fs::read_dir(src)? {
        let e = e?;
        let p = e.path();
        let to = dst.join(e.file_name());
        let kind = e.file_type()?;
        if kind.is_dir() {
            copy_tree(&p, &to, full)?;
        } else if kind.is_file() && e.metadata()?.nlink() == 1 {
            if full {
                let mut input = File::open(&p)?;
                let mut output = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&to)?;
                let mut buf = [0u8; 131072];
                loop {
                    let n = input.read(&mut buf)?;
                    if n == 0 {
                        break;
                    }
                    output.write_all(&buf[..n])?;
                }
            } else {
                clone_one(&p, &to)?;
            }
            File::open(to)?.sync_all()?;
        } else {
            return fail("unsafe_path", "links and special files cannot be copied");
        }
    }
    sync_dir(dst)
}
