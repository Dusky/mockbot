use crate::state::{AppState, IrcControlCmd, LiveEvent};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::process::Stdio;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::mpsc;
use tracing::{error, info, warn};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VoicePresetInfo {
    pub code: String,
    pub label: String,
    pub kind: String, // "custom_npz", "builtin", "random"
}

#[derive(Debug, Clone)]
pub enum TtsRequest {
    Synthesize {
        seq: u64,
        channel: String,
        text: String,
        chat_text: String,
        voice_preset: String,
        text_temp: f64,
        waveform_temp: f64,
        drift_enabled: bool,
        delayed_chat_trigger: Option<String>,
    },
    ResetDrift {
        channel: String,
    },
    CloneAudio {
        audio_path: String,
        voice_name: String,
        transcript: String,
    },
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct WorkerMsg {
    event: String,
    #[serde(default)]
    id: String,
    #[serde(default)]
    ok: bool,
    #[serde(default)]
    device: String,
    #[serde(default)]
    load_secs: f64,
    #[serde(default)]
    output_path: String,
    #[serde(default)]
    voice_preset: String,
    #[serde(default)]
    audio_secs: f64,
    #[serde(default)]
    gen_secs: f64,
    #[serde(default)]
    error: String,
    #[serde(default)]
    message: String,
}

pub fn list_available_voices() -> Vec<VoicePresetInfo> {
    let mut voices = vec![VoicePresetInfo {
        code: "random".to_string(),
        label: "Unhinged Random Bark Hallucination".to_string(),
        kind: "random".to_string(),
    }];

    // Discover custom .npz voices in ./voices
    let voices_dir = Path::new("/home/matt/ansv2/voices");
    if voices_dir.is_dir() {
        let mut custom_names: Vec<String> = Vec::new();
        if let Ok(entries) = fs::read_dir(voices_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.extension().and_then(|e| e.to_str()) == Some("npz") {
                    if let Some(stem) = p.file_stem().and_then(|s| s.to_str()) {
                        custom_names.push(stem.to_string());
                    }
                }
            }
        }
        custom_names.sort();
        for stem in custom_names {
            let friendly = stem
                .split("-24-")
                .next()
                .unwrap_or(&stem)
                .replace('_', " ");
            voices.push(VoicePresetInfo {
                code: stem.clone(),
                label: format!("Custom: {}", friendly),
                kind: "custom_npz".to_string(),
            });
        }
    }

    // Built-in Bark speaker presets
    for i in 0..=9 {
        voices.push(VoicePresetInfo {
            code: format!("v2/en_speaker_{}", i),
            label: format!("Bark v2 Speaker #{}", i),
            kind: "builtin".to_string(),
        });
    }

    voices
}

pub async fn run_bark_worker_loop(
    state: Arc<AppState>,
    mut rx: mpsc::UnboundedReceiver<TtsRequest>,
) {
    let python_bin = "/home/matt/ansv/ansv-bot/.venv/bin/python";
    let worker_script = "/home/matt/ansv2/bark_worker.py";

    if !Path::new(python_bin).exists() || !Path::new(worker_script).exists() {
        warn!(
            "Bark TTS worker dependencies not found ({} or {})",
            python_bin, worker_script
        );
        return;
    }

    loop {
        state.log_system("INFO", "Spawning persistent Bark TTS worker (suno/bark-small)...");
        let child_res = Command::new(python_bin)
            .arg("-u")
            .arg(worker_script)
            .env("HF_HOME", "/home/matt/ansv/ansv-bot/.hf_cache")
            .env("ANSV2_VOICES_DIR", "/home/matt/ansv2/voices")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn();

        let mut child = match child_res {
            Ok(c) => c,
            Err(e) => {
                error!("Failed to spawn bark_worker.py: {}", e);
                tokio::time::sleep(std::time::Duration::from_secs(15)).await;
                continue;
            }
        };

        let Some(mut stdin) = child.stdin.take() else {
            continue;
        };
        let Some(stdout) = child.stdout.take() else {
            continue;
        };
        let mut lines = BufReader::new(stdout).lines();

        // Wait for worker "ready" line
        let mut worker_ready = false;
        while let Ok(Some(line)) = lines.next_line().await {
            if let Ok(msg) = serde_json::from_str::<WorkerMsg>(&line) {
                if msg.event == "ready" {
                    worker_ready = true;
                    state.tts_ready.store(true, Ordering::Relaxed);
                    state.log_system(
                        "INFO",
                        format!(
                            "Bark TTS ready on {} (loaded in {:.2}s)",
                            msg.device, msg.load_secs
                        ),
                    );
                    info!("Bark TTS worker ready on {}", msg.device);
                    break;
                } else if msg.event == "error" {
                    state.log_system("ERROR", format!("Bark worker error: {}", msg.message));
                    break;
                }
            }
        }

        if !worker_ready {
            state.tts_ready.store(false, Ordering::Relaxed);
            let _ = child.kill().await;
            tokio::time::sleep(std::time::Duration::from_secs(15)).await;
            continue;
        }

        // Process queued jobs sequentially
        while let Some(job) = rx.recv().await {
            state.tts_busy.store(true, Ordering::Relaxed);
            match job {
                TtsRequest::ResetDrift { channel } => {
                    let clean_chan = channel.trim().trim_start_matches('#').to_lowercase();
                    let payload = serde_json::json!({
                        "cmd": "reset_drift",
                        "channel": clean_chan,
                    });
                    if stdin
                        .write_all(format!("{}\n", payload).as_bytes())
                        .await
                        .is_err()
                    {
                        break;
                    }
                    let _ = stdin.flush().await;
                    let _ = lines.next_line().await;
                    state.log_system(
                        "INFO",
                        format!("Reset Bark voice drift for #{}", clean_chan),
                    );
                }
                TtsRequest::CloneAudio {
                    audio_path,
                    voice_name,
                    transcript,
                } => {
                    let payload = serde_json::json!({
                        "cmd": "clone_audio",
                        "audio_path": audio_path,
                        "voice_name": voice_name,
                        "transcript": transcript,
                    });
                    if stdin
                        .write_all(format!("{}\n", payload).as_bytes())
                        .await
                        .is_err()
                    {
                        break;
                    }
                    let _ = stdin.flush().await;
                    while let Ok(Some(line)) = lines.next_line().await {
                        if let Ok(msg) = serde_json::from_str::<WorkerMsg>(&line) {
                            if msg.event == "clone_done" {
                                if msg.ok {
                                    state.log_system(
                                        "INFO",
                                        format!("Cloned new Bark .npz voice '{}'", voice_name),
                                    );
                                } else {
                                    state.log_system(
                                        "WARN",
                                        format!("Voice clone failed: {}", msg.error),
                                    );
                                }
                                break;
                            }
                        }
                    }
                }
                TtsRequest::Synthesize {
                    seq,
                    channel,
                    text,
                    chat_text,
                    voice_preset,
                    text_temp,
                    waveform_temp,
                    drift_enabled,
                    delayed_chat_trigger,
                } => {
                    let _ = state.tts_queue_len.fetch_update(
                        Ordering::Relaxed,
                        Ordering::Relaxed,
                        |v| Some(v.saturating_sub(1)),
                    );
                    if seq < state.tts_clear_seq.load(Ordering::Relaxed) {
                        state.tts_busy.store(false, Ordering::Relaxed);
                        continue;
                    }
                    let clean_chan = channel.trim().trim_start_matches('#').to_lowercase();
                    let ts_str = chrono::Local::now().format("%Y%m%d-%H%M%S-%3f").to_string();
                    let rel_path = format!("outputs/{}/{}-{}.wav", clean_chan, clean_chan, ts_str);
                    let abs_path = format!("/home/matt/ansv2/{}", rel_path);

                    let job_payload = serde_json::json!({
                        "cmd": "synthesize",
                        "id": ts_str,
                        "channel": clean_chan,
                        "text": text,
                        "voice_preset": voice_preset,
                        "text_temp": text_temp,
                        "waveform_temp": waveform_temp,
                        "drift_enabled": drift_enabled,
                        "output_path": abs_path,
                    });

                    if stdin
                        .write_all(format!("{}\n", job_payload).as_bytes())
                        .await
                        .is_err()
                    {
                        break;
                    }
                    let _ = stdin.flush().await;

                    while let Ok(Some(line)) = lines.next_line().await {
                        if let Ok(msg) = serde_json::from_str::<WorkerMsg>(&line) {
                            if msg.event == "job_done" {
                                if msg.ok {
                                    if delayed_chat_trigger.is_some() {
                                        let _ = state.irc_cmd_tx.send(IrcControlCmd::SendPrivmsg {
                                            channel: clean_chan.clone(),
                                            text: chat_text.clone(),
                                        });
                                    }

                                    if let Ok(row) = state.db.insert_tts_log(
                                        &clean_chan,
                                        &msg.voice_preset,
                                        &text,
                                        &rel_path,
                                        msg.audio_secs,
                                    ) {
                                        state.emit_event(LiveEvent::TtsGenerated {
                                            id: row.id,
                                            timestamp: row.created_at,
                                            channel: clean_chan.clone(),
                                            voice_preset: msg.voice_preset,
                                            text: text.clone(),
                                            audio_url: format!("/{}", rel_path),
                                            duration_secs: msg.audio_secs,
                                            gen_secs: msg.gen_secs,
                                        });
                                        if let Ok(old_files) =
                                            state.db.prune_old_tts_logs(&clean_chan, 50)
                                        {
                                            for p in old_files {
                                                let wav = format!("/home/matt/ansv2/{}", p);
                                                let _ = std::fs::remove_file(&wav);
                                                let _ = std::fs::remove_file(format!("{}.npz", wav));
                                            }
                                        }
                                    }
                                } else {
                                    if delayed_chat_trigger.is_some() {
                                        let _ = state.irc_cmd_tx.send(IrcControlCmd::SendPrivmsg {
                                            channel: clean_chan.clone(),
                                            text: chat_text.clone(),
                                        });
                                    }
                                    state.log_system(
                                        "WARN",
                                        format!(
                                            "Bark TTS failed for #{}: {}",
                                            clean_chan, msg.error
                                        ),
                                    );
                                }
                                break;
                            }
                        }
                    }
                }
            }
            state.tts_busy.store(false, Ordering::Relaxed);
        }

        state.tts_ready.store(false, Ordering::Relaxed);
        state.tts_busy.store(false, Ordering::Relaxed);
        let _ = child.kill().await;
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    }
}
