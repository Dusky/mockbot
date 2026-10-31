pub mod config;
pub mod db;
pub mod importer;
pub mod markov;
pub mod state;
pub mod tts;
pub mod tui;
pub mod twitch;
pub mod web;

use anyhow::Result;
use clap::{Parser, Subcommand};
use config::AppConfig;
use db::Database;
use markov::{BrainWeight, GenerationParams, MarkovEngine, MessageFilter};
use state::AppState;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::info;

#[derive(Parser)]
#[command(
    name = "ansv2",
    version = "0.2.0",
    about = "Advanced Multi-Channel Multi-Brain Markov Twitch Bot (Daemon, TUI, Public/Admin WebUI)"
)]
struct Cli {
    /// Path to configuration TOML file
    #[arg(short, long, default_value = "config.toml")]
    config: String,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    /// Run interactive Terminal UI (with embedded Twitch Bot + WebUI daemon)
    Tui {
        /// Run in offline/sandbox mode without connecting to Twitch IRC
        #[arg(long)]
        offline: bool,
        /// Print a static snapshot of tab (1, 2, or 3) to stdout and exit
        #[arg(long)]
        dump: Option<usize>,
    },
    /// Run headless daemon (Twitch Bot + Public/Admin WebUI + API)
    Daemon {
        /// Run in offline/sandbox mode without connecting to Twitch IRC
        #[arg(long)]
        offline: bool,
    },
    /// Import legacy corpus logs, markovify JSON caches, and channel configs from ansv-bot
    Import {
        /// Path to legacy ansv-bot directory
        #[arg(short, long, default_value = "/home/matt/ansv/ansv-bot")]
        path: PathBuf,
    },
    /// Generate test Markov sentences from the command line
    Generate {
        /// Channel name (uses that channel's configured brain mix)
        #[arg(short, long)]
        channel: Option<String>,
        /// Explicit brain or weighted mix (e.g. "channel:firestarman=0.7,global=0.3")
        #[arg(short, long)]
        brain: Option<String>,
        /// Markov N-gram order (1, 2, or 3)
        #[arg(short, long, default_value_t = 2)]
        order: usize,
        /// Sampling temperature (0.3 coherent .. 1.0 normal .. 2.0 chaos)
        #[arg(short, long, default_value_t = 1.0)]
        temp: f64,
        /// Optional seed word/topic (uses bidirectional seeded generation)
        #[arg(short, long)]
        seed: Option<String>,
        /// Number of sentences to generate
        #[arg(short = 'n', long, default_value_t = 5)]
        count: usize,
    },
    /// Show corpus statistics for all brains and configured channels
    Stats,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let is_tui = matches!(cli.command, None | Some(Commands::Tui { .. }));

    if !is_tui {
        tracing_subscriber::fmt()
            .with_env_filter(
                tracing_subscriber::EnvFilter::try_from_default_env()
                    .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
            )
            .init();
    }

    let cfg = AppConfig::load_or_create(&cli.config)?;
    let db = Arc::new(Database::open(&cfg.storage.db_path)?);
    let _ = db.ensure_default_users(&cfg.twitch.owner, &cfg.web.admin_token);
    let _ = db.import_legacy_tts_logs(Path::new("/home/matt/ansv/ansv-bot/messages.db"));
    let filter = MessageFilter::new(cfg.markov.strip_urls, Some(&cfg.markov.badwords_file));
    let mut engine = MarkovEngine::new(filter);

    // Auto-import legacy ansv-bot data on first run if database is empty
    if db.corpus_total_rows()? == 0 {
        let legacy_dir = Path::new("/home/matt/ansv/ansv-bot");
        if legacy_dir.exists() {
            if !is_tui {
                info!("Database is empty; auto-importing legacy corpus from /home/matt/ansv/ansv-bot...");
            }
            let _ = importer::import_legacy_directory(legacy_dir, &db, &mut engine)?;
        }
    } else {
        let (loaded_lines, elapsed_ms) = db.hydrate_engine(&mut engine)?;
        if !is_tui {
            info!(
                "Hydrated {} corpus instances across {} brains in {} ms (vocab: {} words)",
                loaded_lines,
                engine.brains.len(),
                elapsed_ms,
                engine.vocab.len()
            );
        }
    }

    match cli.command.unwrap_or(Commands::Tui {
        offline: false,
        dump: None,
    }) {
        Commands::Import { path } => {
            let summary = importer::import_legacy_directory(&path, &db, &mut engine)?;
            println!(
                "✅ Imported {} channels, {} brains, and {} corpus lines from {:?}",
                summary.channels_imported, summary.brains_created, summary.lines_imported, path
            );
        }
        Commands::Stats => {
            print_stats(&db, &engine)?;
        }
        Commands::Generate {
            channel,
            brain,
            order,
            temp,
            seed,
            count,
        } => {
            let (irc_tx, _irc_rx) = mpsc::unbounded_channel();
            let (tts_tx, _tts_rx) = mpsc::unbounded_channel();
            let state = AppState::new(cfg.clone(), db, engine, irc_tx, tts_tx)?;
            run_cli_generate(&state, channel, brain, order, temp, seed, count);
        }
        Commands::Daemon { offline } => {
            let (irc_tx, irc_rx) = mpsc::unbounded_channel();
            let (tts_tx, tts_rx) = mpsc::unbounded_channel();
            let state = AppState::new(cfg, db, engine, irc_tx, tts_tx)?;

            let tts_state = state.clone();
            tokio::spawn(async move {
                tts::run_bark_worker_loop(tts_state, tts_rx).await;
            });

            if !offline {
                let twitch_state = state.clone();
                tokio::spawn(async move {
                    twitch::run_twitch_loop(twitch_state, irc_rx).await;
                });
            } else {
                state.log_system("INFO", "Started in offline/sandbox mode (--offline)");
            }

            web::run_web_server(state).await?;
        }
        Commands::Tui { offline, dump } => {
            let (irc_tx, irc_rx) = mpsc::unbounded_channel();
            let (tts_tx, tts_rx) = mpsc::unbounded_channel();
            let port = cfg
                .web
                .bind_addr
                .rsplit(':')
                .next()
                .and_then(|p| p.parse::<u16>().ok())
                .unwrap_or(5002);
            let local_daemon_addr = format!("127.0.0.1:{}", port);
            let daemon_already_running =
                tokio::net::TcpStream::connect(&local_daemon_addr).await.is_ok();

            let state = AppState::new(cfg, db, engine, irc_tx, tts_tx)?;

            if let Some(tab_num) = dump {
                if daemon_already_running {
                    use std::sync::atomic::Ordering;
                    if let Some(body) =
                        http_get_local(&local_daemon_addr, "/api/status", &state.config.web.admin_token).await
                    {
                        if let Ok(snap) = serde_json::from_str::<state::StatusSnapshot>(&body) {
                            state.irc_connected.store(snap.irc_connected, Ordering::Relaxed);
                            let mut chans = state.channels.write().unwrap();
                            for rt in snap.channels {
                                chans.insert(rt.record.channel_name.clone(), rt);
                            }
                        }
                    }
                    if let Some(body) =
                        http_get_local(&local_daemon_addr, "/api/events", &state.config.web.admin_token).await
                    {
                        if let Ok(events) = serde_json::from_str::<Vec<state::LiveEvent>>(&body) {
                            let mut recent = state.recent_events.write().unwrap();
                            for ev in events {
                                recent.push_back(ev);
                            }
                        }
                    }
                }
                print!("{}", tui::render_snapshot_text(state, tab_num));
                return Ok(());
            }

            if daemon_already_running {
                state.log_system(
                    "INFO",
                    format!("Attached TUI to running daemon on {}", local_daemon_addr),
                );
                let bridge_state = state.clone();
                tokio::spawn(async move {
                    run_daemon_attach_bridge(bridge_state, local_daemon_addr, irc_rx).await;
                });
            } else {
                let tts_state = state.clone();
                tokio::spawn(async move {
                    tts::run_bark_worker_loop(tts_state, tts_rx).await;
                });

                let web_state = state.clone();
                tokio::spawn(async move {
                    let _ = web::run_web_server(web_state).await;
                });

                if !offline {
                    let twitch_state = state.clone();
                    tokio::spawn(async move {
                        twitch::run_twitch_loop(twitch_state, irc_rx).await;
                    });
                } else {
                    state.log_system("INFO", "Started in offline/sandbox mode (--offline)");
                }
            }

            tui::run_tui(state).await?;
        }
    }

    Ok(())
}

fn print_stats(db: &Database, engine: &MarkovEngine) -> Result<()> {
    println!("=== ANSV2 MULTI-BRAIN INVENTORY ===");
    println!(
        "{:<26} {:<10} {:>10} {:>10} {:>10} {:>20}",
        "BRAIN", "KIND", "SENTENCES", "TOKENS", "VOCAB", "O1 / O2 / O3 STATES"
    );
    println!("{}", "-".repeat(92));
    for b in engine.list_brain_stats() {
        println!(
            "{:<26} {:<10} {:>10} {:>10} {:>10} {:>6} / {:>6} / {:>6}",
            b.name,
            format!("{:?}", b.kind),
            b.sentence_count,
            b.total_tokens,
            b.unique_tokens,
            b.states_order_1,
            b.states_order_2,
            b.states_order_3
        );
    }
    println!("\nShared Vocabulary Size: {} unique words\n", engine.vocab.len());

    println!("=== CONFIGURED CHANNELS ===");
    println!(
        "{:<18} {:<7} {:<7} {:<9} {:<12} {:<7} {:<30}",
        "CHANNEL", "JOIN", "VOICE", "->GLOBAL", "CADENCE", "ORDER", "BRAIN MIX"
    );
    println!("{}", "-".repeat(92));
    for ch in db.list_channels()? {
        let mix_str: Vec<String> = ch
            .brain_mix
            .iter()
            .map(|m| format!("{}:{:.0}%", m.brain, m.weight * 100.0))
            .collect();
        println!(
            "#{:<17} {:<7} {:<7} {:<9} {:<12} {:<7} {:<30}",
            ch.channel_name,
            if ch.join_enabled { "yes" } else { "no" },
            if ch.voice_enabled { "on" } else { "muted" },
            if ch.contribute_to_global { "yes" } else { "no" },
            format!("{}L / {}m", ch.lines_between_messages, ch.time_between_messages),
            format!("O{}", ch.markov_order),
            mix_str.join(", ")
        );
    }
    Ok(())
}

fn run_cli_generate(
    state: &Arc<AppState>,
    channel: Option<String>,
    brain_spec: Option<String>,
    order: usize,
    temperature: f64,
    seed: Option<String>,
    count: usize,
) {
    let brains = if let Some(spec) = brain_spec {
        let mut parsed = Vec::new();
        for part in spec.split(',') {
            if let Some((b, w_s)) = part.split_once('=') {
                if let Ok(w) = w_s.parse::<f64>() {
                    parsed.push(BrainWeight {
                        brain: b.trim().to_string(),
                        weight: w,
                    });
                }
            } else {
                parsed.push(BrainWeight {
                    brain: part.trim().to_string(),
                    weight: 1.0,
                });
            }
        }
        parsed
    } else if let Some(ref ch) = channel {
        let clean = ch.trim().trim_start_matches('#').to_lowercase();
        let chans = state.channels.read().unwrap();
        if let Some(rt) = chans.get(&clean) {
            rt.record.brain_mix.clone()
        } else {
            vec![
                BrainWeight {
                    brain: format!("channel:{}", clean),
                    weight: 0.70,
                },
                BrainWeight {
                    brain: "global".to_string(),
                    weight: 0.30,
                },
            ]
        }
    } else {
        vec![BrainWeight {
            brain: "global".to_string(),
            weight: 1.0,
        }]
    };

    let mut params = GenerationParams {
        brains,
        order: order.clamp(1, 3),
        temperature: temperature.clamp(0.25, 3.0),
        seed,
        min_words: state.config.markov.min_words,
        max_words: state.config.markov.max_words,
        max_chars: 450,
        max_overlap_ratio: state.config.markov.max_overlap_ratio,
        max_overlap_total: state.config.markov.max_overlap_total,
        max_attempts: state.config.markov.max_attempts,
        recent_outputs: Vec::new(),
        blocked_words: Vec::new(),
    };

    let eng = state.engine.read().unwrap();
    for i in 1..=count {
        match eng.generate(&params) {
            Some(res) => {
                params.recent_outputs.push(res.text.clone());
                println!(
                    "[{}] {}  (brains: {}, words: {}, attempts: {}, seeded: {})",
                    i,
                    res.text,
                    res.brains_used.join("+"),
                    res.word_count,
                    res.attempts_used,
                    res.seeded
                );
            }
            None => {
                println!("[{}] <failed to generate sentence>", i);
            }
        }
    }
}

async fn run_daemon_attach_bridge(
    state: Arc<AppState>,
    addr: String,
    mut irc_rx: mpsc::UnboundedReceiver<state::IrcControlCmd>,
) {
    use std::sync::atomic::Ordering;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let mut poll_interval = tokio::time::interval(std::time::Duration::from_millis(1000));

    loop {
        tokio::select! {
            _ = poll_interval.tick() => {
                if let Some(body) = http_get_local(&addr, "/api/status", &state.config.web.admin_token).await {
                    if let Ok(snap) = serde_json::from_str::<state::StatusSnapshot>(&body) {
                        state.irc_connected.store(snap.irc_connected, Ordering::Relaxed);
                        state.tts_ready.store(snap.tts_ready, Ordering::Relaxed);
                        state.tts_busy.store(snap.tts_busy, Ordering::Relaxed);
                        let mut chans = state.channels.write().unwrap();
                        for rt in snap.channels {
                            chans.insert(rt.record.channel_name.clone(), rt);
                        }
                    }
                }
                if let Some(body) = http_get_local(&addr, "/api/events", &state.config.web.admin_token).await {
                    if let Ok(events) = serde_json::from_str::<Vec<state::LiveEvent>>(&body) {
                        let mut recent = state.recent_events.write().unwrap();
                        recent.clear();
                        for ev in events {
                            recent.push_back(ev);
                        }
                    }
                }
            }
            Some(cmd) = irc_rx.recv() => {
                if let Ok(json_body) = serde_json::to_string(&cmd) {
                    if let Ok(mut stream) = tokio::net::TcpStream::connect(&addr).await {
                        let req = format!(
                            "POST /api/admin/irc_cmd HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nX-Admin-Token: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                            addr,
                            state.config.web.admin_token,
                            json_body.len(),
                            json_body
                        );
                        let _ = stream.write_all(req.as_bytes()).await;
                        let mut buf = Vec::new();
                        let _ = stream.read_to_end(&mut buf).await;
                    }
                }
            }
        }
    }
}

async fn http_get_local(addr: &str, path: &str, admin_token: &str) -> Option<String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut stream = tokio::net::TcpStream::connect(addr).await.ok()?;
    let req = format!(
        "GET {} HTTP/1.1\r\nHost: {}\r\nX-Admin-Token: {}\r\nConnection: close\r\n\r\n",
        path, addr, admin_token
    );
    stream.write_all(req.as_bytes()).await.ok()?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw).await.ok()?;
    let (_, body) = raw.split_once("\r\n\r\n")?;
    Some(body.to_string())
}
