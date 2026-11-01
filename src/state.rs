use crate::config::AppConfig;
use crate::db::{ChannelRecord, Database};
use crate::markov::{BrainStats, GenerationParams, GenerationResult, MarkovEngine};
use crate::tts::TtsRequest;
use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, RwLock};
use tokio::sync::{broadcast, mpsc};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum LiveEvent {
    ChatMessage {
        timestamp: i64,
        channel: String,
        author: String,
        color: Option<String>,
        is_mod: bool,
        is_broadcaster: bool,
        content: String,
        learned: bool,
    },
    BotResponse {
        timestamp: i64,
        channel: String,
        trigger: String,
        text: String,
        brains_used: Vec<String>,
        attempts: usize,
    },
    SystemLog {
        timestamp: i64,
        level: String,
        message: String,
    },
    ChannelStatusChanged {
        channel: String,
        joined: bool,
    },
    TtsGenerated {
        id: i64,
        timestamp: i64,
        channel: String,
        voice_preset: String,
        text: String,
        audio_url: String,
        duration_secs: f64,
        gen_secs: f64,
    },
    TtsSkip {
        channel: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum IrcControlCmd {
    Join(String),
    Part(String),
    SendPrivmsg { channel: String, text: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChannelRuntime {
    pub record: ChannelRecord,
    pub currently_joined: bool,
    #[serde(default)]
    pub is_stream_live: bool,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub avatar_url: String,
    #[serde(default)]
    pub bio: String,
    #[serde(default)]
    pub followers: u32,
    #[serde(default)]
    pub stream_title: String,
    #[serde(default)]
    pub stream_game: String,
    #[serde(default)]
    pub stream_viewers: u32,
    pub lines_since_last_spoke: u32,
    pub last_spoke_epoch: i64,
    pub last_activity_epoch: i64,
    #[serde(default)]
    pub last_command_epoch: i64,
    pub total_messages_seen: u64,
    pub total_bot_responses: u64,
    #[serde(default)]
    pub recent_keywords: VecDeque<String>,
    #[serde(default)]
    pub recent_bot_outputs: VecDeque<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StatusSnapshot {
    pub bot_nick: String,
    pub owner: String,
    pub irc_connected: bool,
    #[serde(default)]
    pub tts_ready: bool,
    #[serde(default)]
    pub tts_busy: bool,
    #[serde(default)]
    pub tts_queue_len: usize,
    pub uptime_secs: i64,
    pub public_join_enabled: bool,
    pub total_channels: usize,
    pub joined_channels: usize,
    pub vocab_size: usize,
    pub channels: Vec<ChannelRuntime>,
    pub brains: Vec<BrainStats>,
}

pub struct AppState {
    pub config: AppConfig,
    pub db: Arc<Database>,
    pub engine: RwLock<MarkovEngine>,
    pub channels: RwLock<HashMap<String, ChannelRuntime>>,
    pub irc_connected: AtomicBool,
    pub tts_ready: AtomicBool,
    pub tts_busy: AtomicBool,
    pub tts_queue_len: AtomicUsize,
    pub tts_clear_seq: AtomicU64,
    pub start_time: i64,
    pub recent_events: RwLock<VecDeque<LiveEvent>>,
    pub event_tx: broadcast::Sender<LiveEvent>,
    pub irc_cmd_tx: mpsc::UnboundedSender<IrcControlCmd>,
    pub tts_tx: mpsc::UnboundedSender<TtsRequest>,
}

impl AppState {
    pub fn new(
        config: AppConfig,
        db: Arc<Database>,
        engine: MarkovEngine,
        irc_cmd_tx: mpsc::UnboundedSender<IrcControlCmd>,
        tts_tx: mpsc::UnboundedSender<TtsRequest>,
    ) -> Result<Arc<Self>> {
        let (event_tx, _) = broadcast::channel(512);
        let now = chrono::Utc::now().timestamp();

        let mut chan_map = HashMap::new();
        for rec in db.list_channels()? {
            chan_map.insert(
                rec.channel_name.clone(),
                ChannelRuntime {
                    record: rec,
                    currently_joined: false,
                    is_stream_live: false,
                    display_name: String::new(),
                    avatar_url: String::new(),
                    bio: String::new(),
                    followers: 0,
                    stream_title: String::new(),
                    stream_game: String::new(),
                    stream_viewers: 0,
                    lines_since_last_spoke: 0,
                    last_spoke_epoch: now,
                    last_activity_epoch: 0,
                    last_command_epoch: 0,
                    total_messages_seen: 0,
                    total_bot_responses: 0,
                    recent_keywords: VecDeque::with_capacity(32),
                    recent_bot_outputs: VecDeque::with_capacity(40),
                },
            );
        }

        // Ensure initial_channels from config exist
        for ch in &config.twitch.initial_channels {
            let clean = ch.trim().trim_start_matches('#').to_lowercase();
            if !clean.is_empty() && !chan_map.contains_key(&clean) {
                let rec = ChannelRecord::default_for(&clean, true, "config");
                db.upsert_channel(&rec)?;
                chan_map.insert(
                    clean,
                    ChannelRuntime {
                        record: rec,
                        currently_joined: false,
                        is_stream_live: false,
                        display_name: String::new(),
                        avatar_url: String::new(),
                        bio: String::new(),
                        followers: 0,
                        stream_title: String::new(),
                        stream_game: String::new(),
                        stream_viewers: 0,
                        lines_since_last_spoke: 0,
                        last_spoke_epoch: now,
                        last_activity_epoch: 0,
                        last_command_epoch: 0,
                        total_messages_seen: 0,
                        total_bot_responses: 0,
                        recent_keywords: VecDeque::with_capacity(32),
                        recent_bot_outputs: VecDeque::with_capacity(40),
                    },
                );
            }
        }

        Ok(Arc::new(Self {
            config,
            db,
            engine: RwLock::new(engine),
            channels: RwLock::new(chan_map),
            irc_connected: AtomicBool::new(false),
            tts_ready: AtomicBool::new(false),
            tts_busy: AtomicBool::new(false),
            tts_queue_len: AtomicUsize::new(0),
            tts_clear_seq: AtomicU64::new(0),
            start_time: now,
            recent_events: RwLock::new(VecDeque::with_capacity(300)),
            event_tx,
            irc_cmd_tx,
            tts_tx,
        }))
    }

    pub fn emit_event(&self, ev: LiveEvent) {
        {
            let mut recent = self.recent_events.write().unwrap();
            if recent.len() >= 300 {
                recent.pop_front();
            }
            recent.push_back(ev.clone());
        }
        let _ = self.event_tx.send(ev);
    }

    pub fn log_system(&self, level: &str, msg: impl Into<String>) {
        self.emit_event(LiveEvent::SystemLog {
            timestamp: chrono::Utc::now().timestamp(),
            level: level.to_string(),
            message: msg.into(),
        });
    }

    pub fn status_snapshot(&self) -> StatusSnapshot {
        let now = chrono::Utc::now().timestamp();
        let mut channels: Vec<ChannelRuntime> = self
            .channels
            .read()
            .unwrap()
            .values()
            .cloned()
            .collect();
        channels.sort_by(|a, b| a.record.channel_name.cmp(&b.record.channel_name));
        let joined_channels = channels.iter().filter(|c| c.currently_joined).count();

        let (vocab_size, brains) = {
            let eng = self.engine.read().unwrap();
            (eng.vocab.len(), eng.list_brain_stats())
        };

        StatusSnapshot {
            bot_nick: self.config.twitch.nickname.clone(),
            owner: self.config.twitch.owner.clone(),
            irc_connected: self.irc_connected.load(Ordering::Relaxed),
            tts_ready: self.tts_ready.load(Ordering::Relaxed),
            tts_busy: self.tts_busy.load(Ordering::Relaxed),
            tts_queue_len: self.tts_queue_len.load(Ordering::Relaxed),
            uptime_secs: (now - self.start_time).max(0),
            public_join_enabled: self.config.web.public_join_enabled,
            total_channels: channels.len(),
            joined_channels,
            vocab_size,
            channels,
            brains,
        }
    }

    pub fn generate_for_channel(
        &self,
        channel_name: &str,
        seed: Option<String>,
    ) -> Option<GenerationResult> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let (brains, order, temperature, recent_outputs, blocked_words) = {
            let chans = self.channels.read().unwrap();
            if let Some(rt) = chans.get(&clean) {
                (
                    rt.record.brain_mix.clone(),
                    rt.record.markov_order,
                    rt.record.temperature,
                    rt.recent_bot_outputs.iter().cloned().collect(),
                    rt.record.blocked_words.clone(),
                )
            } else {
                (
                    vec![
                        crate::markov::BrainWeight {
                            brain: format!("channel:{}", clean),
                            weight: 0.70,
                        },
                        crate::markov::BrainWeight {
                            brain: "global".to_string(),
                            weight: 0.30,
                        },
                    ],
                    self.config.markov.default_order,
                    1.0,
                    Vec::new(),
                    Vec::new(),
                )
            }
        };

        let params = GenerationParams {
            brains,
            order,
            temperature,
            seed,
            min_words: self.config.markov.min_words,
            max_words: self.config.markov.max_words,
            max_chars: 450,
            max_overlap_ratio: self.config.markov.max_overlap_ratio,
            max_overlap_total: self.config.markov.max_overlap_total,
            max_attempts: self.config.markov.max_attempts,
            recent_outputs,
            blocked_words,
        };

        let eng = self.engine.read().unwrap();
        eng.generate(&params)
    }

    pub fn speak_in_channel(
        &self,
        channel_name: &str,
        trigger: &str,
        seed: Option<String>,
    ) -> Option<GenerationResult> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let res = self.generate_for_channel(&clean, seed)?;
        self.dispatch_generated_message(&clean, trigger, res)
    }

    pub fn speak_with_custom_brains_in_channel(
        &self,
        channel_name: &str,
        brains: Vec<crate::markov::BrainWeight>,
        trigger: &str,
        seed: Option<String>,
    ) -> Option<GenerationResult> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let (order, temperature, recent_outputs, blocked_words) = {
            let chans = self.channels.read().unwrap();
            if let Some(rt) = chans.get(&clean) {
                (
                    rt.record.markov_order,
                    rt.record.temperature,
                    rt.recent_bot_outputs.iter().cloned().collect(),
                    rt.record.blocked_words.clone(),
                )
            } else {
                (
                    self.config.markov.default_order,
                    1.0,
                    Vec::new(),
                    Vec::new(),
                )
            }
        };

        let params = GenerationParams {
            brains,
            order,
            temperature,
            seed,
            min_words: self.config.markov.min_words,
            max_words: self.config.markov.max_words,
            max_chars: 450,
            max_overlap_ratio: self.config.markov.max_overlap_ratio,
            max_overlap_total: self.config.markov.max_overlap_total,
            max_attempts: self.config.markov.max_attempts,
            recent_outputs,
            blocked_words,
        };

        let res = {
            let eng = self.engine.read().unwrap();
            eng.generate(&params)?
        };
        self.dispatch_generated_message(&clean, trigger, res)
    }

    pub fn mock_chatter_in_channel(
        &self,
        channel_name: &str,
        chatter: &str,
        seed: Option<String>,
    ) -> Option<GenerationResult> {
        let clean_chatter = chatter.trim().trim_start_matches('@').to_lowercase();
        let lines = self.db.get_author_lines(&clean_chatter, 500).ok()?;
        if lines.len() < 3 {
            return None;
        }
        let user_brain_name = format!("user:{}", clean_chatter);
        {
            let mut eng = self.engine.write().unwrap();
            eng.ensure_brain(
                &user_brain_name,
                crate::markov::BrainKind::Custom,
                &format!("Chatter brain for @{}", clean_chatter),
            );
            let targets = [user_brain_name.clone()];
            for l in &lines {
                eng.learn_sanitized(l, &targets);
            }
        }
        let clean_chan = channel_name.trim().trim_start_matches('#').to_lowercase();
        let brains = vec![
            crate::markov::BrainWeight {
                brain: user_brain_name.clone(),
                weight: 0.85,
            },
            crate::markov::BrainWeight {
                brain: format!("channel:{}", clean_chan),
                weight: 0.15,
            },
        ];
        self.speak_with_custom_brains_in_channel(
            &clean_chan,
            brains,
            &format!("mock:@{}", clean_chatter),
            seed,
        )
    }

    fn dispatch_generated_message(
        &self,
        clean_channel: &str,
        trigger: &str,
        mut res: GenerationResult,
    ) -> Option<GenerationResult> {
        res.text = crate::markov::MessageFilter::sanitize_outbound(&res.text);
        if res.text.is_empty() {
            return None;
        }
        let now = chrono::Utc::now().timestamp();

        let (tts_enabled, tts_delay_enabled, voice_preset, cursedness, text_temp, waveform_temp, drift_enabled) = {
            let mut chans = self.channels.write().unwrap();
            if let Some(rt) = chans.get_mut(clean_channel) {
                // Hard 2-second per-channel anti-burst floor
                if trigger != "manual" && now - rt.last_spoke_epoch < 2 {
                    return None;
                }
                rt.lines_since_last_spoke = 0;
                rt.last_spoke_epoch = now;
                rt.total_bot_responses += 1;
                if rt.recent_bot_outputs.len() >= 40 {
                    rt.recent_bot_outputs.pop_front();
                }
                rt.recent_bot_outputs.push_back(res.text.clone());
                (
                    rt.record.tts_enabled,
                    rt.record.tts_delay_enabled,
                    rt.record.voice_preset.clone(),
                    rt.record.bark_cursedness,
                    rt.record.bark_text_temp,
                    rt.record.bark_waveform_temp,
                    rt.record.bark_drift_enabled,
                )
            } else {
                (false, false, "v2/en_speaker_5".to_string(), 0, 0.70, 0.70, false)
            }
        };

        // Check if the primary brain used for this message has a persona voice assigned
        let brain_voice_override = {
            let eng = self.engine.read().unwrap();
            res.brains_used
                .first()
                .and_then(|bname| eng.brains.get(bname))
                .map(|b| b.default_voice.clone())
                .filter(|v| !v.is_empty())
        };
        let voice_preset = brain_voice_override.unwrap_or(voice_preset);

        let tts_text = Self::apply_bark_cursedness(&res.text, cursedness);
        let worker_ready = self.tts_ready.load(Ordering::Relaxed);
        let q_len = self.tts_queue_len.load(Ordering::Relaxed);
        let seq = self.tts_clear_seq.load(Ordering::Relaxed);
        if tts_enabled && worker_ready && tts_delay_enabled && q_len < 6 {
            self.tts_queue_len.fetch_add(1, Ordering::Relaxed);
            let _ = self.tts_tx.send(TtsRequest::Synthesize {
                seq,
                channel: clean_channel.to_string(),
                text: tts_text,
                chat_text: res.text.clone(),
                voice_preset,
                text_temp,
                waveform_temp,
                drift_enabled,
                delayed_chat_trigger: Some(trigger.to_string()),
            });
        } else {
            let _ = self.irc_cmd_tx.send(IrcControlCmd::SendPrivmsg {
                channel: clean_channel.to_string(),
                text: res.text.clone(),
            });
            if tts_enabled && worker_ready && q_len < 6 {
                self.tts_queue_len.fetch_add(1, Ordering::Relaxed);
                let _ = self.tts_tx.send(TtsRequest::Synthesize {
                    seq,
                    channel: clean_channel.to_string(),
                    text: tts_text,
                    chat_text: res.text.clone(),
                    voice_preset,
                    text_temp,
                    waveform_temp,
                    drift_enabled,
                    delayed_chat_trigger: None,
                });
            }
        }

        let _ = self
            .db
            .log_bot_response(clean_channel, trigger, &res.text, &res.brains_used);
        self.emit_event(LiveEvent::BotResponse {
            timestamp: now,
            channel: clean_channel.to_string(),
            trigger: trigger.to_string(),
            text: res.text.clone(),
            brains_used: res.brains_used.clone(),
            attempts: res.attempts_used,
        });

        Some(res)
    }

    pub fn queue_tts(
        &self,
        channel_name: &str,
        text: &str,
        voice_override: Option<String>,
    ) -> Result<()> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let safe_text = crate::markov::MessageFilter::sanitize_outbound(text);
        if safe_text.is_empty() {
            return Err(anyhow!("TTS text is empty after sanitization."));
        }
        {
            let eng = self.engine.read().unwrap();
            if !eng.filter.is_safe(&safe_text) {
                return Err(anyhow!("TTS text blocked by safety filter."));
            }
        }
        let (default_vp, text_temp, waveform_temp, drift_enabled, blocked_words) = {
            let chans = self.channels.read().unwrap();
            if let Some(rt) = chans.get(&clean) {
                (
                    rt.record.voice_preset.clone(),
                    rt.record.bark_text_temp,
                    rt.record.bark_waveform_temp,
                    rt.record.bark_drift_enabled,
                    rt.record.blocked_words.clone(),
                )
            } else {
                ("v2/en_speaker_5".to_string(), 0.70, 0.70, false, Vec::new())
            }
        };
        let lower_t = safe_text.to_lowercase();
        if blocked_words.iter().any(|bw| !bw.is_empty() && lower_t.contains(bw)) {
            return Err(anyhow!("TTS text contains a channel-blocked word."));
        }
        let voice_preset = voice_override.unwrap_or(default_vp);
        let q_len = self.tts_queue_len.load(Ordering::Relaxed);
        if q_len >= 6 {
            return Err(anyhow!(
                "TTS queue is full ({} pending clips). Wait a moment or clear queue.",
                q_len
            ));
        }
        let seq = self.tts_clear_seq.load(Ordering::Relaxed);
        self.tts_queue_len.fetch_add(1, Ordering::Relaxed);

        self.tts_tx
            .send(TtsRequest::Synthesize {
                seq,
                channel: clean,
                text: safe_text.clone(),
                chat_text: safe_text,
                voice_preset,
                text_temp,
                waveform_temp,
                drift_enabled,
                delayed_chat_trigger: None,
            })
            .map_err(|_| anyhow!("Bark TTS worker queue is closed"))?;
        Ok(())
    }

    pub fn clear_tts_queue(&self, channel_name: &str) {
        self.tts_clear_seq.fetch_add(1, Ordering::Relaxed);
        self.tts_queue_len.store(0, Ordering::Relaxed);
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        self.emit_event(LiveEvent::TtsSkip { channel: clean });
    }

    pub fn reset_bark_drift(&self, channel_name: &str) -> Result<()> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        self.tts_tx
            .send(TtsRequest::ResetDrift { channel: clean })
            .map_err(|_| anyhow!("Bark TTS worker queue is closed"))?;
        Ok(())
    }

    pub fn clone_audio_voice(
        &self,
        audio_path: String,
        voice_name: String,
        transcript: String,
    ) -> Result<()> {
        self.tts_tx
            .send(TtsRequest::CloneAudio {
                audio_path,
                voice_name,
                transcript,
            })
            .map_err(|_| anyhow!("Bark TTS worker queue is closed"))?;
        Ok(())
    }

    pub fn rebuild_brain_from_db(&self, brain_name: &str) -> Result<usize> {
        let lines = self.db.get_brain_lines(brain_name)?;
        let mut eng = self.engine.write().unwrap();
        if let Some(brain) = eng.brains.get_mut(brain_name) {
            brain.clear();
        }
        let targets = [brain_name.to_string()];
        let mut count = 0usize;
        for line in &lines {
            if eng.learn_sanitized(line, &targets) {
                count += 1;
            }
        }
        Ok(count)
    }

    /// Add or join a channel (used by Admin UI, TUI, Chat Commands, and Public Self-Service Website).
    pub fn add_or_join_channel(
        &self,
        raw_channel: &str,
        source: &str,
        contribute_to_global: bool,
    ) -> Result<ChannelRecord> {
        let clean = Self::validate_channel_name(raw_channel)?;
        let now = chrono::Utc::now().timestamp();

        let rec = {
            let mut chans = self.channels.write().unwrap();
            if let Some(existing) = chans.get_mut(&clean) {
                existing.record.join_enabled = true;
                self.db.upsert_channel(&existing.record)?;
                existing.record.clone()
            } else {
                if source == "public_web" && chans.len() >= self.config.web.max_public_channels {
                    return Err(anyhow!(
                        "Maximum channel capacity ({}) reached",
                        self.config.web.max_public_channels
                    ));
                }
                let new_rec = ChannelRecord::default_for(&clean, contribute_to_global, source);
                self.db.upsert_channel(&new_rec)?;
                chans.insert(
                    clean.clone(),
                    ChannelRuntime {
                        record: new_rec.clone(),
                        currently_joined: false,
                        is_stream_live: false,
                        display_name: String::new(),
                        avatar_url: String::new(),
                        bio: String::new(),
                        followers: 0,
                        stream_title: String::new(),
                        stream_game: String::new(),
                        stream_viewers: 0,
                        lines_since_last_spoke: 0,
                        last_spoke_epoch: now,
                        last_activity_epoch: 0,
                        last_command_epoch: 0,
                        total_messages_seen: 0,
                        total_bot_responses: 0,
                        recent_keywords: VecDeque::with_capacity(32),
                        recent_bot_outputs: VecDeque::with_capacity(40),
                    },
                );
                new_rec
            }
        };

        {
            let mut eng = self.engine.write().unwrap();
            let chan_brain = format!("channel:{}", clean);
            eng.ensure_brain(
                &chan_brain,
                crate::markov::BrainKind::Channel,
                &format!("Channel brain for #{}", clean),
            );
            let _ = self.db.upsert_brain_meta(
                &chan_brain,
                crate::markov::BrainKind::Channel,
                &format!("Channel brain for #{}", clean),
            );
        }

        let _ = self.irc_cmd_tx.send(IrcControlCmd::Join(clean.clone()));
        self.log_system(
            "INFO",
            format!("Channel #{} enabled/joined (source: {})", clean, source),
        );
        Ok(rec)
    }

    pub fn part_channel(&self, raw_channel: &str, remove_from_db: bool) -> Result<()> {
        let clean = Self::validate_channel_name(raw_channel)?;
        {
            let mut chans = self.channels.write().unwrap();
            if remove_from_db {
                chans.remove(&clean);
                self.db.delete_channel(&clean)?;
            } else if let Some(rt) = chans.get_mut(&clean) {
                rt.record.join_enabled = false;
                rt.currently_joined = false;
                self.db.upsert_channel(&rt.record)?;
            }
        }
        let _ = self.irc_cmd_tx.send(IrcControlCmd::Part(clean.clone()));
        self.emit_event(LiveEvent::ChannelStatusChanged {
            channel: clean.clone(),
            joined: false,
        });
        self.log_system("INFO", format!("Left channel #{}", clean));
        Ok(())
    }

    pub fn validate_channel_name(raw: &str) -> Result<String> {
        let clean = raw.trim().trim_start_matches('#').to_lowercase();
        if clean.len() < 2 || clean.len() > 25 {
            return Err(anyhow!("Channel name must be between 2 and 25 characters"));
        }
        if !clean
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
        {
            return Err(anyhow!(
                "Channel name must contain only letters, numbers, and underscores"
            ));
        }
        Ok(clean)
    }

    fn apply_bark_cursedness(text: &str, cursedness_pct: u32) -> String {
        use rand::seq::SliceRandom;
        use rand::Rng;
        if cursedness_pct == 0 {
            return text.to_string();
        }
        let mut rng = rand::thread_rng();
        if rng.gen_range(0..100) >= cursedness_pct {
            return text.to_string();
        }
        let roll: u32 = rng.gen_range(0..100);
        if roll < 20 {
            format!("♪ {} ♪", text)
        } else {
            let tags = [
                "[laughs]",
                "[sighs]",
                "[gasps]",
                "[clears throat]",
                "... [laughs]",
                "... [sighs]",
            ];
            let tag = tags.choose(&mut rng).copied().unwrap_or("[laughs]");
            if rng.gen_bool(0.5) {
                format!("{} {}", tag, text)
            } else {
                format!("{} {}", text, tag)
            }
        }
    }
}
