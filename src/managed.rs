//! Opt-in, cooperative lifecycle for one owned application service group.
//! The backend gate is held for every transition; hooks inherit it on exec.
use crate::lifecycle::{Result, fail};
use crate::paired::read;
use crate::storage::atomic_json;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    os::unix::process::{CommandExt, ExitStatusExt},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hooks {
    pub start: Vec<String>,
    pub stop: Vec<String>,
    pub status: Vec<String>,
    pub timeout_seconds: u64,
}
impl Hooks {
    pub(crate) fn validate(&self) -> Result<()> {
        for argv in [&self.start, &self.stop, &self.status] {
            if argv.is_empty() || argv[0].is_empty() || argv.iter().any(|s| s.contains('\0')) {
                return fail(
                    "project_config",
                    "managed hooks must be nonempty argument arrays",
                );
            }
        }
        if !(1..=3600).contains(&self.timeout_seconds) {
            return fail("project_config", "managed timeout_seconds must be 1–3600");
        }
        Ok(())
    }
}
#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Phase {
    Stopped,
    Running,
    Pending,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    format: u32,
    phase: Phase,
    owned: bool,
}
fn record(state: &Path) -> Result<Record> {
    let path = state.join("managed.json");
    let r = if path.try_exists()? {
        read(&path)?
    } else {
        Record {
            format: 1,
            phase: Phase::Stopped,
            owned: false,
        }
    };
    if r.format != 1
        || (r.phase == Phase::Running && !r.owned)
        || (r.phase == Phase::Stopped && r.owned)
    {
        return fail("managed_metadata", "invalid managed lifecycle record");
    }
    Ok(r)
}
pub(crate) fn idle(state: &Path) -> Result<()> {
    match record(state)?.phase {
        Phase::Stopped => Ok(()),
        Phase::Running => fail(
            "managed_running",
            "managed app is running; use project app-stop before run/task or maintenance",
        ),
        Phase::Pending => fail(
            "managed_pending",
            "managed lifecycle interrupted or failed; run project recover before commands or edits",
        ),
    }
}
pub(crate) fn view(state: &Path) -> Result<Value> {
    Ok(serde_json::to_value(record(state)?)?)
}

pub(crate) enum Action<'a> {
    Start,
    Pause,
    Capture(&'a str),
    Restore(&'a str),
    Recover,
    Stop,
}
pub(crate) struct Context {
    pub root: PathBuf,
    pub state: PathBuf,
    pub hooks: Option<Hooks>,
    pub gate: i32,
}
pub(crate) trait Backend {
    fn context(&self) -> Context;
    fn check(&self) -> Result<()>;
    fn start_command(&mut self, argv: &[String]) -> Result<Command>;
    fn operation(&mut self, action: &Action<'_>) -> Result<Value>;
}
impl Context {
    fn save(&self, phase: Phase, owned: bool) -> Result<()> {
        atomic_json(
            &self.state.join("managed.json"),
            &Record {
                format: 1,
                phase,
                owned,
            },
        )
    }
    fn hook(&self, argv: &[String]) -> Command {
        let mut c = Command::new(&argv[0]);
        c.args(&argv[1..]).current_dir(&self.root);
        c
    }
    fn run(&self, mut c: Command) -> Result<i32> {
        let fd = self.gate;
        c.env("FORKLIFT_MANAGED_STATE", &self.state)
            .env("FORKLIFT_GATE_FD", fd.to_string())
            // Hooks must log privately themselves if diagnostics are needed.
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        unsafe {
            c.pre_exec(move || {
                let flags = libc::fcntl(fd, libc::F_GETFD);
                if flags < 0 || libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = c.spawn()?;
        let deadline =
            Instant::now() + Duration::from_secs(self.hooks.as_ref().unwrap().timeout_seconds);
        loop {
            if let Some(status) = child.try_wait()? {
                return Ok(status
                    .code()
                    .unwrap_or_else(|| 128 + status.signal().unwrap_or(1)));
            }
            if Instant::now() >= deadline {
                // Never release exclusion by killing a possibly mid-write hook.
                // Its inherited gate and durable pending record outlive this caller.
                return fail(
                    "managed_timeout",
                    "hook timed out; it was not killed and retains the gate until exit; then run project recover",
                );
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }
    fn running(&self) -> Result<bool> {
        match self.run(self.hook(&self.hooks.as_ref().unwrap().status))? {
            0 => Ok(true),
            3 => Ok(false),
            _ => fail(
                "managed_status",
                "status hook must return 0 for running or 3 for fully stopped",
            ),
        }
    }
    fn stop(&self) -> Result<()> {
        if self.run(self.hook(&self.hooks.as_ref().unwrap().stop))? != 0 {
            return fail(
                "managed_stop",
                "stop hook failed; work was not declared quiescent; run project recover",
            );
        }
        if self.running()? {
            return fail(
                "managed_stop",
                "stop hook returned but status still reports running; run project recover",
            );
        }
        Ok(())
    }
    fn start<B: Backend>(&self, backend: &mut B) -> Result<()> {
        self.save(Phase::Pending, true)?;
        let command = backend.start_command(&self.hooks.as_ref().unwrap().start)?;
        if self.run(command)? != 0 || !self.running()? {
            return fail(
                "managed_start",
                "start hook failed or app is not running; saved/restored state is retained; run project recover",
            );
        }
        fault("started");
        self.save(Phase::Running, true)
    }
}

fn fault(stage: &str) {
    #[cfg(feature = "test-failpoints")]
    if std::env::var("FORKLIFT_MANAGED_TEST_FAILPOINT").as_deref() == Ok(stage) {
        std::process::exit(99);
    }
    let _ = stage;
}

/// No lock is released between hooks, database/source work and resumption.
pub(crate) fn perform<B: Backend>(backend: &mut B, action: Action<'_>) -> Result<Value> {
    let c = backend.context();
    if c.hooks.is_none() {
        return match action {
            Action::Start | Action::Pause => fail(
                "project_config",
                "configure managed hooks before setup to use app-start/app-stop",
            ),
            _ => backend.operation(&action),
        };
    }
    let r = record(&c.state)?;
    if r.phase == Phase::Pending && !matches!(action, Action::Recover) {
        return fail(
            "managed_pending",
            "managed lifecycle failed or interrupted; run project recover",
        );
    }
    if !matches!(action, Action::Recover | Action::Stop | Action::Pause) {
        backend.check()?;
    }
    // A status hook cannot mutate the application. Do not adopt externally
    // started services: only start intent recorded here establishes ownership.
    c.save(Phase::Pending, r.owned)?;
    // Owned recovery must also handle partial startup where status is unknown.
    // The idempotent stop hook is the authority; require stopped status after it.
    let running = if r.owned && matches!(action, Action::Recover) {
        c.stop()?;
        false
    } else {
        c.running()?
    };
    if running && !r.owned {
        // Retain pending without ownership: recovery must not stop/adopt it.
        return fail(
            "managed_unowned",
            "hook reports an app not started by Forklift; stop it externally before proceeding",
        );
    }
    if matches!(action, Action::Start) {
        if running {
            c.save(Phase::Running, true)?;
        } else {
            c.start(backend)?;
        }
        return Ok(json!({"managed_app":"running"}));
    }
    if running {
        c.stop()?;
    }
    fault("paused");
    let mut result = match action {
        Action::Pause => json!({}),
        _ => backend.operation(&action)?,
    };
    if running && matches!(action, Action::Capture(_) | Action::Restore(_)) {
        c.start(backend)?;
        result["managed_app"] = json!("running");
    } else {
        c.save(Phase::Stopped, false)?;
        result["managed_app"] = json!("stopped");
    }
    if result.get("managed").is_some() {
        result["managed"] = view(&c.state)?;
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hook_contract_is_explicit_and_bounded() {
        assert!(serde_json::from_str::<Hooks>(r#"{"start":["true"]}"#).is_err());
        let mut h = Hooks {
            start: vec!["true".into()],
            stop: vec!["true".into()],
            status: vec!["false".into()],
            timeout_seconds: 30,
        };
        assert!(h.validate().is_ok());
        for timeout in [0, 3601, u64::MAX] {
            h.timeout_seconds = timeout;
            assert!(h.validate().is_err());
        }
        h.timeout_seconds = 1;
        for argv in [
            vec![],
            vec![String::new()],
            vec!["sh".into(), "bad\0argument".into()],
        ] {
            h.stop = argv;
            assert!(h.validate().is_err());
        }
    }
    #[test]
    fn unknown_or_inconsistent_records_fail_closed() -> Result<()> {
        let state =
            std::env::temp_dir().join(format!("forklift-managed-{}", crate::storage::random_id()?));
        crate::storage::private_dir(&state)?;
        assert!(idle(&state).is_ok());
        for data in [
            json!({"format":2,"phase":"stopped","owned":false}),
            json!({"format":1,"phase":"running","owned":false}),
            json!({"format":1,"phase":"stopped","owned":true}),
            json!({"format":1,"phase":"unknown","owned":false}),
        ] {
            atomic_json(&state.join("managed.json"), &data)?;
            assert!(idle(&state).is_err());
        }
        std::fs::remove_dir_all(state)?;
        Ok(())
    }
}
