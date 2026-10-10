use clap::{Parser, Subcommand, ValueEnum};
use forklift::lifecycle::{Result, Store};
use forklift::paired::Workspace;
use std::{ffi::OsString, path::PathBuf, time::Instant};

#[derive(Parser)]
#[command(version, about = "Isolated local PostgreSQL 17 branches")]
struct Args {
    #[arg(long, global = true, default_value = ".forklift")]
    home: PathBuf,
    #[arg(long, global = true)]
    pg_bin: Option<PathBuf>,
    #[arg(long, global = true)]
    json: bool,
    /// Backend for cached or legacy physical checkpoints; no silent fallback.
    #[arg(long, global = true, value_enum, default_value = "apfs")]
    copy_mode: CopyMode,
    /// Bypass the materialized cache and reconstruct directly from objects.
    #[arg(long, global = true)]
    no_cache: bool,
    #[command(subcommand)]
    command: Action,
}
#[derive(Clone, Copy, ValueEnum)]
enum CopyMode {
    Apfs,
    Full,
}
#[derive(Subcommand)]
enum Action {
    DeleteBranch {
        name: String,
        #[arg(long)]
        yes: bool,
    },
    DeleteCheckpoint {
        name: String,
        #[arg(long)]
        yes: bool,
    },
    Gc {
        #[arg(long)]
        yes: bool,
    },
    /// Configure and rewind selected source paths in a stable Git worktree.
    Project {
        #[arg(long, default_value = ".")]
        root: PathBuf,
        #[command(subcommand)]
        action: ProjectAction,
    },
    /// Capture or rewind an owned application source/database pair.
    Pair {
        workspace: PathBuf,
        #[command(subcommand)]
        action: PairAction,
    },
    /// Create an empty, stopped cluster.
    Init { name: String },
    /// Replace a branch with a trusted pg_dump archive. Requires --yes.
    Import {
        name: String,
        #[arg(long)]
        dump: PathBuf,
        #[arg(long)]
        yes: bool,
    },
    /// Save a local immutable stopped-cluster snapshot; resume the source if running.
    Checkpoint {
        branch: String,
        #[arg(long)]
        name: String,
    },
    /// Create a stopped branch from a checkpoint (optional source checks ancestry).
    Branch {
        source: Option<String>,
        #[arg(long)]
        from: String,
        #[arg(long)]
        name: String,
    },
    /// Start a branch with normal PostgreSQL durability.
    Start { name: String },
    /// Cleanly stop a branch and disconnect its clients.
    Stop { name: String },
    /// Print a libpq connection string for a running branch.
    Connection { name: String },
    /// Export a self-contained checkpoint directory (destination must not exist).
    Export {
        name: String,
        #[arg(long)]
        output: PathBuf,
    },
    /// Restore an exported checkpoint as a new stopped branch.
    Restore {
        bundle: PathBuf,
        #[arg(long)]
        name: String,
    },
    /// Set or show the per-store running-cluster cap (default 3).
    Config {
        #[arg(long)]
        max_running: Option<usize>,
    },
    /// Report retained generation/object/cache storage, including incomplete work.
    Stats,
    /// Preview reclaimable generations/cache; --yes permanently removes them.
    Cleanup {
        #[arg(long)]
        yes: bool,
    },
    /// Reconcile interrupted operations and list branches and checkpoints.
    List,
    /// Discard branch changes by replacing it with a checkpoint. Requires --yes.
    Reset {
        name: String,
        #[arg(long)]
        to: String,
        #[arg(long)]
        yes: bool,
    },
}
#[derive(Subcommand)]
enum ProjectAction {
    /// Remove the shared seed configuration and release its checkpoint pin.
    Unconfigure {
        #[arg(long)]
        yes: bool,
    },
    /// Check configuration, seed and local connectivity; never contacts hosted services.
    Doctor,
    Prune {
        #[arg(long)]
        yes: bool,
    },
    Forget {
        name: String,
        #[arg(long)]
        yes: bool,
    },
    /// Register this repository's machine-local store and seed checkpoint.
    Configure {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        checkpoint: String,
    },
    /// Create an isolated database binding; repeated completed setup is idempotent.
    Setup,
    Capture {
        name: String,
    },
    /// Pause unmanaged writers; opt-in managed hooks drain and resume the app.
    Restore {
        name: String,
    },
    Recover,
    Inspect,
    Stop,
    /// Start the explicitly configured managed application group.
    AppStart,
    /// Gracefully stop the managed application group, leaving the database available.
    AppStop,
    /// Run a configured argv command (prepare, verify, start, etc.).
    Task {
        name: String,
    },
    Run {
        #[arg(required = true, last = true)]
        command: Vec<OsString>,
    },
}
fn execute_project(
    args: &Args,
    root: &std::path::Path,
    action: &ProjectAction,
) -> Result<serde_json::Value> {
    use forklift::project::Project;
    if forklift::supabase::selected(root)? {
        use forklift::supabase::SupabaseProject;
        let full = matches!(args.copy_mode, CopyMode::Full);
        if matches!(action, ProjectAction::Doctor) {
            return SupabaseProject::doctor(root, full);
        }
        if matches!(
            action,
            ProjectAction::Configure { .. } | ProjectAction::Unconfigure { .. }
        ) {
            return Err(forklift::lifecycle::Error::new(
                "project_config",
                "Supabase manages its local seed from migrations and seed.sql; use project setup",
            ));
        }
        let mut p = if matches!(action, ProjectAction::Setup) {
            SupabaseProject::setup(root, args.pg_bin.as_deref(), full)?
        } else {
            SupabaseProject::open(root, full)?
        };
        return match action {
            ProjectAction::Setup | ProjectAction::Inspect => p.inspect(),
            ProjectAction::Capture { name } => p.capture(name),
            ProjectAction::Restore { name } => p.restore(name),
            ProjectAction::Recover => p.recover(),
            ProjectAction::Stop => p.stop(),
            ProjectAction::AppStart => p.app_start(),
            ProjectAction::AppStop => p.app_stop(),
            ProjectAction::Prune { yes } => p.prune(*yes),
            ProjectAction::Forget { name, yes } => p.forget(name, *yes),
            ProjectAction::Task { name } => std::process::exit(p.task(name)?),
            ProjectAction::Run { command } => std::process::exit(p.run(command)?),
            _ => unreachable!(),
        };
    }
    if let ProjectAction::Configure { store, checkpoint } = action {
        return Project::configure(root, store, checkpoint);
    }
    if let ProjectAction::Unconfigure { yes } = action {
        return Project::unconfigure(root, *yes);
    }
    let full = matches!(args.copy_mode, CopyMode::Full);
    if matches!(action, ProjectAction::Doctor) {
        return Project::doctor(root, args.pg_bin.as_deref(), full, !args.no_cache);
    }
    let mut p = if matches!(action, ProjectAction::Setup) {
        Project::setup(root, args.pg_bin.as_deref(), full, !args.no_cache)?
    } else {
        Project::open(root, args.pg_bin.as_deref(), full, !args.no_cache)?
    };
    match action {
        ProjectAction::Setup | ProjectAction::Inspect => p.inspect(),
        ProjectAction::Capture { name } => p.capture(name),
        ProjectAction::Restore { name } => p.restore(name),
        ProjectAction::Recover => p.recover(),
        ProjectAction::Stop => p.stop(),
        ProjectAction::AppStart => p.app_start(),
        ProjectAction::AppStop => p.app_stop(),
        ProjectAction::Task { name } => std::process::exit(p.task(name)?),
        ProjectAction::Run { command } => std::process::exit(p.run(command)?),
        ProjectAction::Prune { yes } => p.prune(*yes),
        ProjectAction::Forget { name, yes } => p.forget(name, *yes),
        ProjectAction::Configure { .. }
        | ProjectAction::Doctor
        | ProjectAction::Unconfigure { .. } => unreachable!(),
    }
}
#[derive(Subcommand)]
enum PairAction {
    Prune {
        #[arg(long)]
        yes: bool,
    },
    Forget {
        name: String,
        #[arg(long)]
        yes: bool,
    },
    /// Create a new owned workspace from source and a saved database checkpoint.
    Init {
        #[arg(long)]
        source: PathBuf,
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        checkpoint: String,
    },
    /// Save source bytes and a matching stopped-database checkpoint.
    Capture { name: String },
    /// Select a fresh source/database pair, retaining the previous generation.
    Restore { name: String },
    /// Resolve a pending capture/restore journal before resuming app access.
    Recover,
    /// Show selected source inventory and saved pairs.
    Inspect,
    /// Park the selected database branch.
    Stop,
    /// Run a synchronous command with DATABASE_URL, retaining the workspace gate.
    Run {
        #[arg(required = true, last = true)]
        command: Vec<OsString>,
    },
}
fn execute_pair(
    args: &Args,
    root: &std::path::Path,
    action: &PairAction,
) -> Result<serde_json::Value> {
    let full = matches!(args.copy_mode, CopyMode::Full);
    if let PairAction::Init {
        source,
        store,
        checkpoint,
    } = action
    {
        return Workspace::initialize(
            root,
            source,
            store,
            checkpoint,
            args.pg_bin.as_deref(),
            full,
            !args.no_cache,
        )?
        .inspect();
    }
    let mut w = Workspace::open(root, args.pg_bin.as_deref(), full, !args.no_cache)?;
    if !matches!(action, PairAction::Recover) {
        w.require_settled()?;
    }
    match action {
        PairAction::Prune { yes } => w.prune(*yes),
        PairAction::Forget { name, yes } => w.forget(name, *yes),
        PairAction::Capture { name } => w.capture(name),
        PairAction::Restore { name } => w.restore(name),
        PairAction::Recover => w.recover(),
        PairAction::Inspect => w.inspect(),
        PairAction::Stop => w.stop(),
        PairAction::Run { command } => {
            let code = w.run(command)?;
            // Child output and exit status are passed through, even with --json.
            std::process::exit(code);
        }
        PairAction::Init { .. } => unreachable!(),
    }
}
fn execute(args: &Args) -> Result<serde_json::Value> {
    if let Action::Project { root, action } = &args.command {
        return execute_project(args, root, action);
    }
    if let Action::Pair { workspace, action } = &args.command {
        return execute_pair(args, workspace, action);
    }
    let mut s = Store::open(
        &args.home,
        args.pg_bin.as_deref(),
        matches!(args.command, Action::Init { .. } | Action::Restore { .. }),
        matches!(args.copy_mode, CopyMode::Full),
    )?;
    s.set_cache_enabled(!args.no_cache);
    if !matches!(
        args.command,
        Action::Cleanup { .. }
            | Action::Gc { .. }
            | Action::DeleteBranch { .. }
            | Action::DeleteCheckpoint { .. }
    ) {
        s.reconcile()?;
    }
    match &args.command {
        Action::Init { name } => s.init(name)?,
        Action::Import { name, dump, yes } => {
            if !yes {
                return forklift::lifecycle::fail(
                    "confirmation_required",
                    "import replaces branch contents; rerun with --yes",
                );
            }
            s.import(name, dump)?;
        }
        Action::Checkpoint { branch, name } => s.checkpoint(branch, name)?,
        Action::Branch { source, from, name } => s.branch(source.as_deref(), from, name)?,
        Action::Start { name } => s.start(name)?,
        Action::Stop { name } => s.stop(name)?,
        Action::Reset { name, to, yes } => {
            if !yes {
                return forklift::lifecycle::fail(
                    "confirmation_required",
                    "reset discards branch changes; rerun with --yes",
                );
            }
            s.reset(name, to)?;
        }
        Action::Connection { name } => return s.connection(name),
        Action::Export { name, output } => s.export(name, output)?,
        Action::Restore { bundle, name } => s.restore(bundle, name)?,
        Action::DeleteBranch { name, yes } => return s.delete_branch(name, *yes),
        Action::DeleteCheckpoint { name, yes } => return s.delete_checkpoint(name, *yes),
        Action::Gc { yes } => return s.gc(*yes),
        Action::Stats => return s.stats(),
        Action::Cleanup { yes } => return s.cleanup(*yes),
        Action::Config { max_running } => s.config(*max_running)?,
        Action::List => {}
        Action::Pair { .. } | Action::Project { .. } => unreachable!(),
    }
    s.view()
}
fn main() {
    let args = match Args::try_parse() {
        Ok(args) => args,
        Err(e) => {
            if std::env::args().any(|a| a == "--json")
                && !matches!(
                    e.kind(),
                    clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
                )
            {
                println!(
                    "{}",
                    serde_json::json!({"ok":false,"error":{"code":"usage","message":e.to_string().chars().take(8000).collect::<String>()}})
                );
                std::process::exit(2);
            }
            e.exit();
        }
    };
    let start = Instant::now();
    match execute(&args) {
        Ok(result) => {
            if args.json {
                println!(
                    "{}",
                    serde_json::json!({"ok":true,"elapsed_ms":start.elapsed().as_millis(),"result":result})
                );
            } else if let Some(connection) = result.get("connection").and_then(|v| v.as_str()) {
                println!("{connection}");
            } else if result.get("candidates").is_some()
                || result.get("total").is_some()
                || matches!(args.command, Action::Pair { .. } | Action::Project { .. })
            {
                println!("{}", serde_json::to_string_pretty(&result).unwrap());
            } else {
                if let Some(branches) = result.get("branches").and_then(|v| v.as_array()) {
                    println!("BRANCH\tACTIVITY\tCHECKPOINT");
                    for b in branches {
                        println!(
                            "{}\t{}\t{}",
                            b["name"].as_str().unwrap(),
                            b["activity"].as_str().unwrap(),
                            b["checkpoint"].as_str().unwrap_or("-")
                        );
                    }
                }
                if let Some(checkpoints) = result.get("checkpoints").and_then(|v| v.as_array()) {
                    for c in checkpoints {
                        println!(
                            "checkpoint {} ({})",
                            c["name"].as_str().unwrap(),
                            c["state"].as_str().unwrap()
                        );
                    }
                }
                if let Some(events) = result.get("events").and_then(|v| v.as_array()) {
                    for event in events {
                        let description = match event["stage"].as_str().unwrap_or("") {
                            "full_copy_and_sync" | "cache_full_copy_and_sync" => {
                                Some("full file copy")
                            }
                            "apfs_clone_and_sync" | "cache_apfs_clone_and_sync" => {
                                Some("APFS clone")
                            }
                            "cache_hit_verify" => Some("verified materialized cache hit"),
                            "cache_build_verify_publish" => {
                                Some("built verified materialized cache")
                            }
                            "object_capture" => Some("compressed object checkpoint"),
                            "object_materialization" | "restore_materialize_and_validate" => {
                                Some("verified object materialization")
                            }
                            _ => None,
                        };
                        if let Some(description) = description {
                            println!("storage: {description}");
                        }
                    }
                }
                println!(
                    "running: {}/{}",
                    result["active_clusters"], result["max_running"]
                );
                println!("completed in {:.3}s", start.elapsed().as_secs_f64());
            }
        }
        Err(e) => {
            if args.json {
                println!(
                    "{}",
                    serde_json::json!({"ok":false,"error":{"code":e.code,"message":e.message}})
                );
            } else {
                eprintln!("{}: {}", e.code, e.message);
            }
            std::process::exit(1);
        }
    }
}
