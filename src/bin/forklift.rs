use clap::{Parser, Subcommand, ValueEnum};
use forklift::lifecycle::{Result, Store};
use std::{path::PathBuf, time::Instant};

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
fn execute(args: &Args) -> Result<serde_json::Value> {
    let mut s = Store::open(
        &args.home,
        args.pg_bin.as_deref(),
        matches!(args.command, Action::Init { .. } | Action::Restore { .. }),
        matches!(args.copy_mode, CopyMode::Full),
    )?;
    s.set_cache_enabled(!args.no_cache);
    s.reconcile()?;
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
        Action::Stats => return s.stats(),
        Action::Config { max_running } => s.config(*max_running)?,
        Action::List => {}
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
            } else if result.get("total").is_some() {
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
