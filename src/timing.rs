//! Opt-in, credential-free wall-clock spans for local checkpoint experiments.
//! Nested spans overlap: subtract child intervals, never sum inclusive durations.
use std::{
    cell::{Cell, RefCell},
    io::Write,
    sync::OnceLock,
    time::Instant,
};

static ENABLED: OnceLock<bool> = OnceLock::new();
static ORIGIN: OnceLock<Instant> = OnceLock::new();
thread_local! {
    static EVENTS: RefCell<Vec<serde_json::Value>> = const { RefCell::new(Vec::new()) };
    static TRUNCATED: Cell<bool> = const { Cell::new(false) };
}

pub struct Span(Option<(&'static str, Instant)>);
pub fn span(phase: &'static str) -> Span {
    if *ENABLED.get_or_init(|| std::env::var("FORKLIFT_TIMING").as_deref() == Ok("1")) {
        ORIGIN.get_or_init(Instant::now);
        Span(Some((phase, Instant::now())))
    } else {
        Span(None)
    }
}
impl Drop for Span {
    fn drop(&mut self) {
        if let Some((phase, start)) = self.0 {
            let elapsed = start.elapsed().as_secs_f64() * 1000.0;
            EVENTS.with(|events| {
                let mut events = events.borrow_mut();
                // Bound diagnostics even for unusually large source trees.
                if events.len() < 200_000 {
                    events.push(serde_json::json!({"phase":phase,
                        "start_ms":start.duration_since(*ORIGIN.get().unwrap()).as_secs_f64()*1000.0,
                        "elapsed_ms":elapsed}));
                } else {
                    TRUNCATED.with(|value| value.set(true));
                }
            });
        }
    }
}
/// Called after the operation, so diagnostic output does not extend its outage.
/// No arguments, paths, SQL, connection strings, or runtime output are recorded.
pub fn flush() {
    EVENTS.with(|events| {
        let mut events = events.borrow_mut();
        if !events.is_empty() {
            let value = serde_json::json!({"forklift_timing":1,"truncated":TRUNCATED.with(|value| value.replace(false)),"spans":*events});
            let _ = writeln!(std::io::stderr().lock(), "{value}");
            events.clear();
        }
    });
}

pub fn capture_stats(stats: &crate::objects::CaptureStats) {
    if *ENABLED.get_or_init(|| std::env::var("FORKLIFT_TIMING").as_deref() == Ok("1")) {
        EVENTS.with(|events| {
            let mut events = events.borrow_mut();
            if events.len() < 200_000 {
                events.push(serde_json::json!({"object_stats":stats}));
            } else {
                TRUNCATED.with(|value| value.set(true));
            }
        });
    }
}
