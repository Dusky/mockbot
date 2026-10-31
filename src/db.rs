use crate::markov::{BrainKind, BrainWeight, MarkovEngine};
use anyhow::Result;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;
use std::time::Instant;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChannelRecord {
    pub channel_name: String,
    pub join_enabled: bool,
    pub voice_enabled: bool,
    pub learn_enabled: bool,
    pub contribute_to_global: bool,
    pub lines_between_messages: u32,
    pub time_between_messages: u32,
    pub markov_order: usize,
    #[serde(default = "default_temp")]
    pub temperature: f64,
    #[serde(default = "default_context_bias")]
    pub context_bias_chance: u32,
    pub reply_mention_chance: u32,
    #[serde(default)]
    pub tts_enabled: bool,
    #[serde(default)]
    pub tts_delay_enabled: bool,
    #[serde(default = "default_voice_preset")]
    pub voice_preset: String,
    pub brain_mix: Vec<BrainWeight>,
    pub custom_learn_brains: Vec<String>,
    pub owner: String,
    pub trusted_users: Vec<String>,
    pub ignored_users: Vec<String>,
    #[serde(default)]
    pub blocked_words: Vec<String>,
    #[serde(default = "default_cooldown")]
    pub command_cooldown_secs: u32,
    #[serde(default)]
    pub only_when_live: bool,
    #[serde(default)]
    pub raid_greet_enabled: bool,
    #[serde(default)]
    pub sub_greet_enabled: bool,
    #[serde(default)]
    pub reward_tts_id: String,
    #[serde(default)]
    pub reward_markov_id: String,
    #[serde(default = "default_cursedness")]
    pub bark_cursedness: u32,
    #[serde(default = "default_bark_temp")]
    pub bark_text_temp: f64,
    #[serde(default = "default_bark_temp")]
    pub bark_waveform_temp: f64,
    #[serde(default)]
    pub bark_drift_enabled: bool,
    #[serde(default)]
    pub trigger_keywords: Vec<String>,
    #[serde(default = "default_fifty")]
    pub trigger_chance: u32,
    #[serde(default)]
    pub custom_commands: Vec<CustomCommandRule>,
    #[serde(default)]
    pub min_bits_tts: u32,
    #[serde(default)]
    pub min_bits_markov: u32,
    #[serde(default)]
    pub obs_key: String,
    #[serde(default)]
    pub obs_idle_img: String,
    #[serde(default)]
    pub obs_talk_img: String,
    #[serde(default)]
    pub private_brain: bool,
    #[serde(default = "default_bot_identity")]
    pub bot_identity: String, // "ansv" or "mockbot"
    pub source: String,
    pub created_at: i64,
}

fn default_bot_identity() -> String {
    "mockbot".to_string()
}

pub fn is_ansv_group_channel(channel_name: &str) -> bool {
    matches!(
        channel_name.trim().trim_start_matches('#').to_lowercase().as_str(),
        "firestarman" | "skip_skipperson" | "egancaerwyn" | "krayshkast" | "aegies" | "imyt"
    )
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CustomCommandRule {
    pub cmd: String,
    pub brain: String,
    pub seed: String,
}

fn default_temp() -> f64 {
    1.0
}
fn default_bark_temp() -> f64 {
    0.70
}
fn default_fifty() -> u32 {
    50
}
fn default_context_bias() -> u32 {
    35
}
fn default_voice_preset() -> String {
    "v2/en_speaker_5".to_string()
}
fn default_cooldown() -> u32 {
    10
}
fn default_cursedness() -> u32 {
    25
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserAccount {
    pub username: String,
    #[serde(skip_serializing)]
    pub password_hash: String,
    pub role: String, // "admin" or "streamer"
    #[serde(default = "default_tier")]
    pub tier: String, // "free" or "pro"
    pub channel_name: String, // "*" for admin, or channel name for streamer
    pub api_token: String,
    #[serde(default = "default_true")]
    pub verified: bool,
    #[serde(default)]
    pub claim_code: String,
    pub created_at: i64,
}

fn default_tier() -> String {
    "pro".to_string()
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TtsLogRow {
    pub id: i64,
    pub channel_name: String,
    pub voice_preset: String,
    pub text_content: String,
    pub file_path: String,
    pub duration_secs: f64,
    pub created_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CorpusLineRow {
    pub id: i64,
    pub brain_name: String,
    pub channel_origin: String,
    pub author: String,
    pub content: String,
    pub created_at: i64,
}

impl ChannelRecord {
    pub fn default_for(channel_name: &str, contribute_to_global: bool, source: &str) -> Self {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let chan_brain = format!("channel:{}", clean);
        Self {
            channel_name: clean.clone(),
            join_enabled: true,
            voice_enabled: true,
            learn_enabled: true,
            contribute_to_global,
            lines_between_messages: 100,
            time_between_messages: 15,
            markov_order: 2,
            temperature: 1.0,
            context_bias_chance: 35,
            reply_mention_chance: 0,
            tts_enabled: false,
            tts_delay_enabled: false,
            voice_preset: "v2/en_speaker_5".to_string(),
            brain_mix: vec![
                BrainWeight {
                    brain: chan_brain,
                    weight: 0.70,
                },
                BrainWeight {
                    brain: "global".to_string(),
                    weight: 0.30,
                },
            ],
            custom_learn_brains: Vec::new(),
            owner: clean.clone(),
            trusted_users: Vec::new(),
            ignored_users: Vec::new(),
            blocked_words: Vec::new(),
            command_cooldown_secs: 10,
            only_when_live: true,
            raid_greet_enabled: false,
            sub_greet_enabled: false,
            reward_tts_id: String::new(),
            reward_markov_id: String::new(),
            bark_cursedness: 25,
            bark_text_temp: 0.70,
            bark_waveform_temp: 0.70,
            bark_drift_enabled: false,
            trigger_keywords: Vec::new(),
            trigger_chance: 50,
            custom_commands: Vec::new(),
            min_bits_tts: 0,
            min_bits_markov: 0,
            obs_key: String::new(),
            obs_idle_img: String::new(),
            obs_talk_img: String::new(),
            private_brain: false,
            bot_identity: if is_ansv_group_channel(&clean) {
                "ansv".to_string()
            } else {
                "mockbot".to_string()
            },
            source: source.to_string(),
            created_at: chrono::Utc::now().timestamp(),
        }
    }

    pub fn learning_target_brains(&self) -> Vec<String> {
        let mut out = Vec::new();
        if self.learn_enabled {
            out.push(format!("channel:{}", self.channel_name));
        }
        if self.contribute_to_global {
            out.push("global".to_string());
        }
        for b in &self.custom_learn_brains {
            if !b.is_empty() && !out.contains(b) {
                out.push(b.clone());
            }
        }
        out
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatLogEntry {
    pub id: i64,
    pub channel: String,
    pub author: String,
    pub content: String,
    pub is_bot: bool,
    pub timestamp: i64,
}

pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA temp_store = MEMORY;
             PRAGMA busy_timeout = 5000;",
        )?;
        let db = Self {
            conn: Mutex::new(conn),
        };
        db.init_schema()?;
        Ok(db)
    }

    fn init_schema(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS channels (
                channel_name TEXT PRIMARY KEY,
                join_enabled INTEGER NOT NULL DEFAULT 1,
                voice_enabled INTEGER NOT NULL DEFAULT 1,
                learn_enabled INTEGER NOT NULL DEFAULT 1,
                contribute_to_global INTEGER NOT NULL DEFAULT 1,
                lines_between_messages INTEGER NOT NULL DEFAULT 50,
                time_between_messages INTEGER NOT NULL DEFAULT 15,
                markov_order INTEGER NOT NULL DEFAULT 2,
                temperature REAL NOT NULL DEFAULT 1.0,
                context_bias_chance INTEGER NOT NULL DEFAULT 35,
                reply_mention_chance INTEGER NOT NULL DEFAULT 100,
                brain_mix_json TEXT NOT NULL,
                custom_learn_brains TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL,
                trusted_users TEXT NOT NULL DEFAULT '',
                ignored_users TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'admin',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS brains_meta (
                name TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS corpus_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brain_name TEXT NOT NULL,
                channel_origin TEXT NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_brain ON corpus_lines(brain_name);
            CREATE INDEX IF NOT EXISTS idx_corpus_channel_time ON corpus_lines(channel_origin, created_at DESC);

            CREATE TABLE IF NOT EXISTS bot_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                response_text TEXT NOT NULL,
                brains_used TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_responses_channel_time ON bot_responses(channel_name, created_at DESC);

            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                api_token TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tts_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                voice_preset TEXT NOT NULL,
                text_content TEXT NOT NULL,
                file_path TEXT NOT NULL,
                duration_secs REAL NOT NULL DEFAULT 0.0,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tts_logs_channel ON tts_logs(channel_name, id DESC);
            ",
        )?;
        // Seamless schema migration for existing DBs
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN temperature REAL NOT NULL DEFAULT 1.0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN context_bias_chance INTEGER NOT NULL DEFAULT 35", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN tts_enabled INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN tts_delay_enabled INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN voice_preset TEXT NOT NULL DEFAULT 'v2/en_speaker_5'", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN blocked_words TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN command_cooldown_secs INTEGER NOT NULL DEFAULT 10", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN only_when_live INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN raid_greet_enabled INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN sub_greet_enabled INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN reward_tts_id TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN reward_markov_id TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN bark_cursedness INTEGER NOT NULL DEFAULT 25", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN bark_text_temp REAL NOT NULL DEFAULT 0.7", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN bark_waveform_temp REAL NOT NULL DEFAULT 0.7", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN bark_drift_enabled INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN trigger_keywords TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN trigger_chance INTEGER NOT NULL DEFAULT 50", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN custom_commands_json TEXT NOT NULL DEFAULT '[]'", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN min_bits_tts INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN min_bits_markov INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN obs_key TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN obs_idle_img TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN obs_talk_img TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN private_brain INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE channels ADD COLUMN bot_identity TEXT NOT NULL DEFAULT 'mockbot'", []);
        let _ = conn.execute(
            "UPDATE channels SET bot_identity = 'ansv' WHERE channel_name IN ('firestarman', 'skip_skipperson', 'egancaerwyn', 'krayshkast', 'aegies', 'imyt') AND bot_identity = 'mockbot'",
            [],
        );
        let _ = conn.execute("ALTER TABLE brains_meta ADD COLUMN default_voice TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 1", []);
        let _ = conn.execute("ALTER TABLE users ADD COLUMN claim_code TEXT NOT NULL DEFAULT ''", []);
        let _ = conn.execute("ALTER TABLE users ADD COLUMN tier TEXT NOT NULL DEFAULT 'pro'", []);
        Ok(())
    }

    pub fn upsert_channel(&self, rec: &ChannelRecord) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        let mix_json = serde_json::to_string(&rec.brain_mix)?;
        let cmds_json = serde_json::to_string(&rec.custom_commands).unwrap_or_else(|_| "[]".to_string());
        let custom_learn = rec.custom_learn_brains.join(",");
        let trusted = rec.trusted_users.join(",");
        let ignored = rec.ignored_users.join(",");
        let blocked = rec.blocked_words.join(",");
        let trig_kw = rec.trigger_keywords.join(",");
        conn.execute(
            "INSERT INTO channels (
                channel_name, join_enabled, voice_enabled, learn_enabled, contribute_to_global,
                lines_between_messages, time_between_messages, markov_order, temperature, context_bias_chance,
                reply_mention_chance, brain_mix_json, custom_learn_brains, owner, trusted_users, ignored_users,
                source, created_at, tts_enabled, tts_delay_enabled, voice_preset, blocked_words, command_cooldown_secs,
                only_when_live, raid_greet_enabled, bark_cursedness, sub_greet_enabled, reward_tts_id, reward_markov_id,
                bark_text_temp, bark_waveform_temp, bark_drift_enabled, trigger_keywords, trigger_chance, custom_commands_json,
                min_bits_tts, min_bits_markov, obs_key, obs_idle_img, obs_talk_img, private_brain, bot_identity
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?24, ?25, ?26, ?27, ?28, ?29, ?30, ?31, ?32, ?33, ?34, ?35, ?36, ?37, ?38, ?39, ?40, ?41, ?42)
            ON CONFLICT(channel_name) DO UPDATE SET
                join_enabled = excluded.join_enabled,
                voice_enabled = excluded.voice_enabled,
                learn_enabled = excluded.learn_enabled,
                contribute_to_global = excluded.contribute_to_global,
                lines_between_messages = excluded.lines_between_messages,
                time_between_messages = excluded.time_between_messages,
                markov_order = excluded.markov_order,
                temperature = excluded.temperature,
                context_bias_chance = excluded.context_bias_chance,
                reply_mention_chance = excluded.reply_mention_chance,
                brain_mix_json = excluded.brain_mix_json,
                custom_learn_brains = excluded.custom_learn_brains,
                owner = excluded.owner,
                trusted_users = excluded.trusted_users,
                ignored_users = excluded.ignored_users,
                source = excluded.source,
                tts_enabled = excluded.tts_enabled,
                tts_delay_enabled = excluded.tts_delay_enabled,
                voice_preset = excluded.voice_preset,
                blocked_words = excluded.blocked_words,
                command_cooldown_secs = excluded.command_cooldown_secs,
                only_when_live = excluded.only_when_live,
                raid_greet_enabled = excluded.raid_greet_enabled,
                bark_cursedness = excluded.bark_cursedness,
                sub_greet_enabled = excluded.sub_greet_enabled,
                reward_tts_id = excluded.reward_tts_id,
                reward_markov_id = excluded.reward_markov_id,
                bark_text_temp = excluded.bark_text_temp,
                bark_waveform_temp = excluded.bark_waveform_temp,
                bark_drift_enabled = excluded.bark_drift_enabled,
                trigger_keywords = excluded.trigger_keywords,
                trigger_chance = excluded.trigger_chance,
                custom_commands_json = excluded.custom_commands_json,
                min_bits_tts = excluded.min_bits_tts,
                min_bits_markov = excluded.min_bits_markov,
                obs_key = excluded.obs_key,
                obs_idle_img = excluded.obs_idle_img,
                obs_talk_img = excluded.obs_talk_img,
                private_brain = excluded.private_brain,
                bot_identity = excluded.bot_identity",
            params![
                rec.channel_name,
                rec.join_enabled as i32,
                rec.voice_enabled as i32,
                rec.learn_enabled as i32,
                rec.contribute_to_global as i32,
                rec.lines_between_messages,
                rec.time_between_messages,
                rec.markov_order as i64,
                rec.temperature,
                rec.context_bias_chance,
                rec.reply_mention_chance,
                mix_json,
                custom_learn,
                rec.owner,
                trusted,
                ignored,
                rec.source,
                rec.created_at,
                rec.tts_enabled as i32,
                rec.tts_delay_enabled as i32,
                rec.voice_preset,
                blocked,
                rec.command_cooldown_secs,
                rec.only_when_live as i32,
                rec.raid_greet_enabled as i32,
                rec.bark_cursedness,
                rec.sub_greet_enabled as i32,
                rec.reward_tts_id,
                rec.reward_markov_id,
                rec.bark_text_temp,
                rec.bark_waveform_temp,
                rec.bark_drift_enabled as i32,
                trig_kw,
                rec.trigger_chance,
                cmds_json,
                rec.min_bits_tts,
                rec.min_bits_markov,
                rec.obs_key,
                rec.obs_idle_img,
                rec.obs_talk_img,
                rec.private_brain as i32,
                rec.bot_identity,
            ],
        )?;
        Ok(())
    }

    pub fn delete_channel(&self, channel_name: &str) -> Result<bool> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let conn = self.conn.lock().unwrap();
        let n = conn.execute("DELETE FROM channels WHERE channel_name = ?1", params![clean])?;
        Ok(n > 0)
    }

    pub fn get_channel(&self, channel_name: &str) -> Result<Option<ChannelRecord>> {
        let clean = channel_name.trim().trim_start_matches('#').to_lowercase();
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT channel_name, join_enabled, voice_enabled, learn_enabled, contribute_to_global,
                    lines_between_messages, time_between_messages, markov_order, reply_mention_chance,
                    brain_mix_json, custom_learn_brains, owner, trusted_users, ignored_users, source, created_at,
                    temperature, context_bias_chance, tts_enabled, tts_delay_enabled, voice_preset,
                    blocked_words, command_cooldown_secs, only_when_live, raid_greet_enabled, bark_cursedness,
                    sub_greet_enabled, reward_tts_id, reward_markov_id,
                    bark_text_temp, bark_waveform_temp, bark_drift_enabled, trigger_keywords, trigger_chance,
                    custom_commands_json, min_bits_tts, min_bits_markov, obs_key, obs_idle_img, obs_talk_img,
                    private_brain, bot_identity
             FROM channels WHERE channel_name = ?1",
        )?;
        let rec = stmt
            .query_row(params![clean], Self::map_channel_row)
            .optional()?;
        Ok(rec)
    }

    pub fn list_channels(&self) -> Result<Vec<ChannelRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT channel_name, join_enabled, voice_enabled, learn_enabled, contribute_to_global,
                    lines_between_messages, time_between_messages, markov_order, reply_mention_chance,
                    brain_mix_json, custom_learn_brains, owner, trusted_users, ignored_users, source, created_at,
                    temperature, context_bias_chance, tts_enabled, tts_delay_enabled, voice_preset,
                    blocked_words, command_cooldown_secs, only_when_live, raid_greet_enabled, bark_cursedness,
                    sub_greet_enabled, reward_tts_id, reward_markov_id,
                    bark_text_temp, bark_waveform_temp, bark_drift_enabled, trigger_keywords, trigger_chance,
                    custom_commands_json, min_bits_tts, min_bits_markov, obs_key, obs_idle_img, obs_talk_img,
                    private_brain, bot_identity
             FROM channels ORDER BY channel_name ASC",
        )?;
        let rows = stmt.query_map([], Self::map_channel_row)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    fn map_channel_row(row: &rusqlite::Row) -> rusqlite::Result<ChannelRecord> {
        let channel_name: String = row.get(0)?;
        let mix_json: String = row.get(9)?;
        let custom_learn_str: String = row.get(10)?;
        let trusted_str: String = row.get(12)?;
        let ignored_str: String = row.get(13)?;
        let blocked_str: String = row.get::<_, String>(21).unwrap_or_default();
        let trig_str: String = row.get::<_, String>(32).unwrap_or_default();
        let cmds_json: String = row.get::<_, String>(34).unwrap_or_default();

        let brain_mix: Vec<BrainWeight> = serde_json::from_str(&mix_json).unwrap_or_else(|_| {
            vec![
                BrainWeight {
                    brain: format!("channel:{}", channel_name),
                    weight: 0.70,
                },
                BrainWeight {
                    brain: "global".to_string(),
                    weight: 0.30,
                },
            ]
        });
        let custom_commands: Vec<CustomCommandRule> =
            serde_json::from_str(&cmds_json).unwrap_or_default();

        let split_csv = |s: &str| -> Vec<String> {
            s.split(',')
                .map(|x| x.trim().to_lowercase())
                .filter(|x| !x.is_empty())
                .collect()
        };

        Ok(ChannelRecord {
            channel_name,
            join_enabled: row.get::<_, i32>(1)? != 0,
            voice_enabled: row.get::<_, i32>(2)? != 0,
            learn_enabled: row.get::<_, i32>(3)? != 0,
            contribute_to_global: row.get::<_, i32>(4)? != 0,
            lines_between_messages: row.get(5)?,
            time_between_messages: row.get(6)?,
            markov_order: (row.get::<_, i64>(7)? as usize).clamp(1, 3),
            temperature: row.get::<_, f64>(16).unwrap_or(1.0).clamp(0.25, 3.0),
            context_bias_chance: row.get::<_, u32>(17).unwrap_or(35).min(100),
            reply_mention_chance: row.get(8)?,
            tts_enabled: row.get::<_, i32>(18).unwrap_or(0) != 0,
            tts_delay_enabled: row.get::<_, i32>(19).unwrap_or(0) != 0,
            voice_preset: row
                .get::<_, String>(20)
                .unwrap_or_else(|_| "v2/en_speaker_5".to_string()),
            brain_mix,
            custom_learn_brains: split_csv(&custom_learn_str),
            owner: row.get(11)?,
            trusted_users: split_csv(&trusted_str),
            ignored_users: split_csv(&ignored_str),
            blocked_words: split_csv(&blocked_str),
            command_cooldown_secs: row.get::<_, u32>(22).unwrap_or(10),
            only_when_live: row.get::<_, i32>(23).unwrap_or(0) != 0,
            raid_greet_enabled: row.get::<_, i32>(24).unwrap_or(0) != 0,
            bark_cursedness: row.get::<_, u32>(25).unwrap_or(25).min(100),
            sub_greet_enabled: row.get::<_, i32>(26).unwrap_or(0) != 0,
            reward_tts_id: row.get::<_, String>(27).unwrap_or_default(),
            reward_markov_id: row.get::<_, String>(28).unwrap_or_default(),
            bark_text_temp: row.get::<_, f64>(29).unwrap_or(0.70).clamp(0.2, 1.5),
            bark_waveform_temp: row.get::<_, f64>(30).unwrap_or(0.70).clamp(0.2, 1.5),
            bark_drift_enabled: row.get::<_, i32>(31).unwrap_or(0) != 0,
            trigger_keywords: split_csv(&trig_str),
            trigger_chance: row.get::<_, u32>(33).unwrap_or(50).min(100),
            custom_commands,
            min_bits_tts: row.get::<_, u32>(35).unwrap_or(0),
            min_bits_markov: row.get::<_, u32>(36).unwrap_or(0),
            obs_key: row.get::<_, String>(37).unwrap_or_default(),
            obs_idle_img: row.get::<_, String>(38).unwrap_or_default(),
            obs_talk_img: row.get::<_, String>(39).unwrap_or_default(),
            private_brain: row.get::<_, i32>(40).unwrap_or(0) != 0,
            bot_identity: row.get::<_, String>(41).unwrap_or_else(|_| "mockbot".to_string()),
            source: row.get(14)?,
            created_at: row.get(15)?,
        })
    }

    pub fn backup_db_snapshot(&self, dest_path: &Path) -> Result<()> {
        if dest_path.exists() {
            let _ = std::fs::remove_file(dest_path);
        }
        let dest_str = dest_path.to_string_lossy();
        let conn = self.conn.lock().unwrap();
        conn.execute("VACUUM INTO ?1", params![dest_str.as_ref()])?;
        Ok(())
    }

    pub fn set_brain_voice(&self, brain_name: &str, voice: &str) -> Result<()> {
        let kind_str = if brain_name == "global" {
            "global"
        } else if brain_name.starts_with("channel:") {
            "channel"
        } else {
            "custom"
        };
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO brains_meta (name, kind, description, created_at, default_voice)
             VALUES (?1, ?2, '', ?3, ?4)
             ON CONFLICT(name) DO UPDATE SET default_voice = excluded.default_voice",
            params![brain_name, kind_str, chrono::Utc::now().timestamp(), voice],
        )?;
        Ok(())
    }

    pub fn upsert_brain_meta(&self, name: &str, kind: BrainKind, description: &str) -> Result<()> {
        let kind_str = match kind {
            BrainKind::Global => "global",
            BrainKind::Channel => "channel",
            BrainKind::Custom => "custom",
        };
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO brains_meta (name, kind, description, created_at)
             VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(name) DO UPDATE SET description = excluded.description",
            params![name, kind_str, description, chrono::Utc::now().timestamp()],
        )?;
        Ok(())
    }

    pub fn delete_brain(&self, brain_name: &str) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let _ = conn.execute("DELETE FROM brains_meta WHERE name = ?1", params![brain_name])?;
        let deleted = conn.execute(
            "DELETE FROM corpus_lines WHERE brain_name = ?1",
            params![brain_name],
        )?;
        Ok(deleted)
    }

    pub fn purge_channel_from_global(&self, channel_origin: &str) -> Result<usize> {
        let clean = channel_origin.trim().trim_start_matches('#').to_lowercase();
        let conn = self.conn.lock().unwrap();
        let deleted = conn.execute(
            "DELETE FROM corpus_lines WHERE brain_name = 'global' AND lower(channel_origin) = ?1",
            params![clean],
        )?;
        Ok(deleted)
    }

    pub fn trim_brain_to_newest(&self, brain_name: &str, keep_newest: usize) -> Result<usize> {
        let keep = keep_newest.max(10) as i64;
        let conn = self.conn.lock().unwrap();
        let deleted = conn.execute(
            "DELETE FROM corpus_lines
             WHERE brain_name = ?1
               AND id NOT IN (
                 SELECT id FROM corpus_lines WHERE brain_name = ?1 ORDER BY id DESC LIMIT ?2
               )",
            params![brain_name, keep],
        )?;
        Ok(deleted)
    }

    pub fn merge_brain_corpus(&self, source_brain: &str, target_brain: &str) -> Result<usize> {
        let now = chrono::Utc::now().timestamp();
        let conn = self.conn.lock().unwrap();
        let inserted = conn.execute(
            "INSERT INTO corpus_lines (brain_name, channel_origin, author, content, created_at)
             SELECT ?2, channel_origin, author, content, ?3
             FROM corpus_lines
             WHERE brain_name = ?1
               AND content NOT IN (SELECT content FROM corpus_lines WHERE brain_name = ?2)",
            params![source_brain, target_brain, now],
        )?;
        Ok(inserted)
    }

    pub fn insert_corpus_line(
        &self,
        brain_name: &str,
        channel_origin: &str,
        author: &str,
        content: &str,
    ) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO corpus_lines (brain_name, channel_origin, author, content, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                brain_name,
                channel_origin,
                author,
                content,
                chrono::Utc::now().timestamp()
            ],
        )?;
        Ok(())
    }

    pub fn insert_corpus_batch(
        &self,
        entries: &[(String, String, String, String)],
    ) -> Result<usize> {
        let mut conn = self.conn.lock().unwrap();
        let tx = conn.transaction()?;
        let now = chrono::Utc::now().timestamp();
        let mut count = 0;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO corpus_lines (brain_name, channel_origin, author, content, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            )?;
            for (brain, chan, author, content) in entries {
                stmt.execute(params![brain, chan, author, content, now])?;
                count += 1;
            }
        }
        tx.commit()?;
        Ok(count)
    }

    pub fn log_bot_response(
        &self,
        channel_name: &str,
        trigger_type: &str,
        response_text: &str,
        brains_used: &[String],
    ) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO bot_responses (channel_name, trigger_type, response_text, brains_used, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                channel_name,
                trigger_type,
                response_text,
                brains_used.join(","),
                chrono::Utc::now().timestamp()
            ],
        )?;
        Ok(())
    }

    pub fn recent_responses(&self, limit: usize) -> Result<Vec<(String, String, String, String, i64)>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT channel_name, trigger_type, response_text, brains_used, created_at
             FROM bot_responses ORDER BY id DESC LIMIT ?1",
        )?;
        let rows = stmt.query_map(params![limit as i64], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    pub fn corpus_total_rows(&self) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let count: i64 =
            conn.query_row("SELECT COUNT(*) FROM corpus_lines", [], |r| r.get(0))?;
        Ok(count as usize)
    }

    pub fn search_corpus_lines(
        &self,
        brain_name: &str,
        query: Option<&str>,
        limit: usize,
    ) -> Result<Vec<CorpusLineRow>> {
        let conn = self.conn.lock().unwrap();
        let lim = limit.clamp(1, 500) as i64;
        let mut out = Vec::new();
        if let Some(q) = query.filter(|s| !s.trim().is_empty()) {
            let pattern = format!("%{}%", q.trim());
            let mut stmt = conn.prepare(
                "SELECT id, brain_name, channel_origin, author, content, created_at
                 FROM corpus_lines
                 WHERE brain_name = ?1 AND content LIKE ?2
                 ORDER BY id DESC LIMIT ?3",
            )?;
            let rows = stmt.query_map(params![brain_name, pattern, lim], |r| {
                Ok(CorpusLineRow {
                    id: r.get(0)?,
                    brain_name: r.get(1)?,
                    channel_origin: r.get(2)?,
                    author: r.get(3)?,
                    content: r.get(4)?,
                    created_at: r.get(5)?,
                })
            })?;
            for r in rows {
                out.push(r?);
            }
        } else {
            let mut stmt = conn.prepare(
                "SELECT id, brain_name, channel_origin, author, content, created_at
                 FROM corpus_lines
                 WHERE brain_name = ?1
                 ORDER BY id DESC LIMIT ?2",
            )?;
            let rows = stmt.query_map(params![brain_name, lim], |r| {
                Ok(CorpusLineRow {
                    id: r.get(0)?,
                    brain_name: r.get(1)?,
                    channel_origin: r.get(2)?,
                    author: r.get(3)?,
                    content: r.get(4)?,
                    created_at: r.get(5)?,
                })
            })?;
            for r in rows {
                out.push(r?);
            }
        }
        Ok(out)
    }

    pub fn delete_corpus_line(&self, id: i64) -> Result<Option<String>> {
        let conn = self.conn.lock().unwrap();
        let brain_opt: Option<String> = conn
            .query_row(
                "SELECT brain_name FROM corpus_lines WHERE id = ?1",
                params![id],
                |r| r.get(0),
            )
            .optional()?;
        if brain_opt.is_some() {
            conn.execute("DELETE FROM corpus_lines WHERE id = ?1", params![id])?;
        }
        Ok(brain_opt)
    }

    pub fn get_brain_lines(&self, brain_name: &str) -> Result<Vec<String>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT content FROM corpus_lines WHERE brain_name = ?1 ORDER BY id ASC",
        )?;
        let rows = stmt.query_map(params![brain_name], |r| r.get::<_, String>(0))?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    pub fn get_author_lines(&self, author: &str, limit: usize) -> Result<Vec<String>> {
        let clean = author.trim().trim_start_matches('@').to_lowercase();
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT DISTINCT content FROM corpus_lines WHERE lower(author) = ?1 ORDER BY id DESC LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![clean, limit as i64], |r| r.get::<_, String>(0))?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    pub fn top_chatters(&self, channel_filter: Option<&str>, limit: usize) -> Result<Vec<(String, usize)>> {
        let conn = self.conn.lock().unwrap();
        let lim = limit.clamp(1, 100) as i64;
        let mut out = Vec::new();
        if let Some(ch) = channel_filter.filter(|s| !s.is_empty() && *s != "*") {
            let clean = ch.trim().trim_start_matches('#').to_lowercase();
            let mut stmt = conn.prepare(
                "SELECT lower(author) as a, COUNT(DISTINCT content) as c
                 FROM corpus_lines
                 WHERE lower(channel_origin) = ?1
                   AND lower(author) NOT IN ('imported', 'markovify_cache', 'admin', 'migrated_user')
                 GROUP BY a ORDER BY c DESC LIMIT ?2",
            )?;
            let rows = stmt.query_map(params![clean, lim], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)? as usize))
            })?;
            for r in rows {
                out.push(r?);
            }
        } else {
            let mut stmt = conn.prepare(
                "SELECT lower(author) as a, COUNT(DISTINCT content) as c
                 FROM corpus_lines
                 WHERE lower(author) NOT IN ('imported', 'markovify_cache', 'admin', 'migrated_user')
                 GROUP BY a ORDER BY c DESC LIMIT ?1",
            )?;
            let rows = stmt.query_map(params![lim], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)? as usize))
            })?;
            for r in rows {
                out.push(r?);
            }
        }
        Ok(out)
    }

    /// Hydrates the in-memory `MarkovEngine` from `brains_meta` and `corpus_lines`.
    pub fn hydrate_engine(&self, engine: &mut MarkovEngine) -> Result<(usize, u128)> {
        let start = Instant::now();
        let conn = self.conn.lock().unwrap();

        // 1. Load brain metadata
        {
            let mut stmt = conn.prepare(
                "SELECT name, kind, description, COALESCE(default_voice, '') FROM brains_meta",
            )?;
            let rows = stmt.query_map([], |r| {
                let name: String = r.get(0)?;
                let kind_s: String = r.get(1)?;
                let desc: String = r.get(2)?;
                let voice: String = r.get(3).unwrap_or_default();
                let kind = match kind_s.as_str() {
                    "global" => BrainKind::Global,
                    "channel" => BrainKind::Channel,
                    _ => BrainKind::Custom,
                };
                Ok((name, kind, desc, voice))
            })?;
            for r in rows {
                let (name, kind, desc, voice) = r?;
                engine.ensure_brain(&name, kind, &desc);
                if !voice.is_empty() {
                    if let Some(b) = engine.brains.get_mut(&name) {
                        b.default_voice = voice;
                    }
                }
            }
        }

        // 2. Stream all corpus lines into the in-memory Markov graphs
        let mut count = 0usize;
        {
            let mut stmt = conn.prepare("SELECT brain_name, content FROM corpus_lines ORDER BY id ASC")?;
            let rows = stmt.query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?;
            for r in rows {
                let (brain_name, content) = r?;
                let targets = [brain_name];
                if engine.learn_sanitized(&content, &targets) {
                    count += 1;
                }
            }
        }

        Ok((count, start.elapsed().as_millis()))
    }

    // ─── USER ACCOUNTS & STREAMER INSTANCE AUTH ───

    pub fn hash_password(username: &str, password: &str) -> String {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h1 = DefaultHasher::new();
        ("ansv2-salt-v1", username.to_lowercase(), password).hash(&mut h1);
        let mut h2 = DefaultHasher::new();
        (password, username.to_lowercase(), h1.finish()).hash(&mut h2);
        format!("{:016x}{:016x}", h1.finish(), h2.finish())
    }

    fn generate_api_token(username: &str) -> String {
        use rand::Rng;
        let mut rng = rand::thread_rng();
        let r1: u64 = rng.gen();
        let r2: u64 = rng.gen();
        format!("tok_{}_{:016x}{:016x}", username.to_lowercase(), r1, r2)
    }

    pub fn ensure_default_users(&self, owner: &str, admin_password: &str) -> Result<()> {
        let count: i64 = {
            let conn = self.conn.lock().unwrap();
            conn.query_row("SELECT COUNT(*) FROM users", [], |r| r.get(0))?
        };
        if count == 0 {
            let _ = self.create_or_update_user("admin", admin_password, "admin", "*")?;
            if !owner.is_empty() && !owner.eq_ignore_ascii_case("admin") {
                let _ = self.create_or_update_user(owner, admin_password, "admin", "*")?;
            }
            let _ = self.create_or_update_user("skip", "skip", "streamer", "skip_skipperson")?;
        }
        Ok(())
    }

    pub fn create_or_update_user(
        &self,
        username: &str,
        password: &str,
        role: &str,
        channel_name: &str,
    ) -> Result<UserAccount> {
        self.create_or_update_user_with_verification(username, password, role, channel_name, true, "")
    }

    pub fn create_or_update_user_with_verification(
        &self,
        username: &str,
        password: &str,
        role: &str,
        channel_name: &str,
        verified: bool,
        claim_code: &str,
    ) -> Result<UserAccount> {
        self.create_or_update_user_full(username, password, role, "pro", channel_name, verified, claim_code)
    }

    pub fn create_or_update_user_full(
        &self,
        username: &str,
        password: &str,
        role: &str,
        tier: &str,
        channel_name: &str,
        verified: bool,
        claim_code: &str,
    ) -> Result<UserAccount> {
        let clean_user = username
            .trim()
            .trim_start_matches('@')
            .trim_start_matches('#')
            .to_lowercase();
        let clean_chan = if role == "admin" {
            "*".to_string()
        } else {
            let ch = channel_name
                .trim()
                .trim_start_matches('@')
                .trim_start_matches('#')
                .to_lowercase();
            if ch.is_empty() {
                clean_user.clone()
            } else {
                ch
            }
        };

        let conn = self.conn.lock().unwrap();
        let existing: Option<(String, String)> = conn
            .query_row(
                "SELECT password_hash, api_token FROM users WHERE username = ?1",
                params![clean_user],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;

        let pw_hash = if password.trim().is_empty() {
            if let Some((ref old_hash, _)) = existing {
                old_hash.clone()
            } else {
                Self::hash_password(&clean_user, &clean_user)
            }
        } else {
            Self::hash_password(&clean_user, password.trim())
        };

        let token = if let Some((_, ref old_tok)) = existing {
            old_tok.clone()
        } else {
            Self::generate_api_token(&clean_user)
        };
        let now = chrono::Utc::now().timestamp();

        conn.execute(
            "INSERT INTO users (username, password_hash, role, channel_name, api_token, created_at, verified, claim_code, tier)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
             ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                role = excluded.role,
                channel_name = excluded.channel_name,
                verified = excluded.verified,
                claim_code = excluded.claim_code,
                tier = excluded.tier",
            params![
                clean_user,
                pw_hash,
                role,
                clean_chan,
                token,
                now,
                verified as i32,
                claim_code,
                tier
            ],
        )?;

        Ok(UserAccount {
            username: clean_user,
            password_hash: pw_hash,
            role: role.to_string(),
            tier: tier.to_string(),
            channel_name: clean_chan,
            api_token: token,
            verified,
            claim_code: claim_code.to_string(),
            created_at: now,
        })
    }

    pub fn verify_channel_claim(&self, channel_name: &str, code: &str) -> Result<bool> {
        let clean_chan = channel_name.trim().trim_start_matches('#').to_lowercase();
        let clean_code = code.trim();
        if clean_code.is_empty() {
            return Ok(false);
        }
        let conn = self.conn.lock().unwrap();
        let n = conn.execute(
            "UPDATE users SET verified = 1, claim_code = '' WHERE channel_name = ?1 AND claim_code = ?2",
            params![clean_chan, clean_code],
        )?;
        Ok(n > 0)
    }

    pub fn authenticate_user(
        &self,
        username: &str,
        password: &str,
    ) -> Result<Option<UserAccount>> {
        let clean_user = username.trim().to_lowercase();
        let expected_hash = Self::hash_password(&clean_user, password);
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT username, password_hash, role, channel_name, api_token, created_at, verified, claim_code, COALESCE(tier, 'pro')
             FROM users WHERE username = ?1",
        )?;
        let acct = stmt
            .query_row(params![clean_user], |r| {
                Ok(UserAccount {
                    username: r.get(0)?,
                    password_hash: r.get(1)?,
                    role: r.get(2)?,
                    channel_name: r.get(3)?,
                    api_token: r.get(4)?,
                    created_at: r.get(5)?,
                    verified: r.get::<_, i32>(6).unwrap_or(1) != 0,
                    claim_code: r.get::<_, String>(7).unwrap_or_default(),
                    tier: r.get::<_, String>(8).unwrap_or_else(|_| "pro".to_string()),
                })
            })
            .optional()?;

        Ok(acct.filter(|a| a.password_hash == expected_hash))
    }

    pub fn get_user_by_token(&self, token: &str) -> Result<Option<UserAccount>> {
        let clean = token.trim();
        if clean.is_empty() {
            return Ok(None);
        }
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT username, password_hash, role, channel_name, api_token, created_at, verified, claim_code, COALESCE(tier, 'pro')
             FROM users WHERE api_token = ?1",
        )?;
        let acct = stmt
            .query_row(params![clean], |r| {
                Ok(UserAccount {
                    username: r.get(0)?,
                    password_hash: r.get(1)?,
                    role: r.get(2)?,
                    channel_name: r.get(3)?,
                    api_token: r.get(4)?,
                    created_at: r.get(5)?,
                    verified: r.get::<_, i32>(6).unwrap_or(1) != 0,
                    claim_code: r.get::<_, String>(7).unwrap_or_default(),
                    tier: r.get::<_, String>(8).unwrap_or_else(|_| "pro".to_string()),
                })
            })
            .optional()?;
        Ok(acct)
    }

    pub fn list_users(&self) -> Result<Vec<UserAccount>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT username, password_hash, role, channel_name, api_token, created_at, verified, claim_code, COALESCE(tier, 'pro')
             FROM users ORDER BY role ASC, username ASC",
        )?;
        let rows = stmt.query_map([], |r| {
            Ok(UserAccount {
                username: r.get(0)?,
                password_hash: r.get(1)?,
                role: r.get(2)?,
                channel_name: r.get(3)?,
                api_token: r.get(4)?,
                created_at: r.get(5)?,
                verified: r.get::<_, i32>(6).unwrap_or(1) != 0,
                claim_code: r.get::<_, String>(7).unwrap_or_default(),
                tier: r.get::<_, String>(8).unwrap_or_else(|_| "pro".to_string()),
            })
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    pub fn purge_author_lines_in_channel(&self, channel: &str, author: &str) -> Result<usize> {
        let clean_chan = channel.trim().trim_start_matches('#').to_lowercase();
        let clean_author = author.trim().trim_start_matches('@').to_lowercase();
        let conn = self.conn.lock().unwrap();
        let deleted = conn.execute(
            "DELETE FROM corpus_lines WHERE lower(channel_origin) = ?1 AND lower(author) = ?2",
            params![clean_chan, clean_author],
        )?;
        Ok(deleted)
    }

    pub fn purge_matching_line_in_channel(&self, channel: &str, content: &str) -> Result<usize> {
        let clean_chan = channel.trim().trim_start_matches('#').to_lowercase();
        let trimmed = content.trim();
        if trimmed.is_empty() {
            return Ok(0);
        }
        let conn = self.conn.lock().unwrap();
        let deleted = conn.execute(
            "DELETE FROM corpus_lines WHERE lower(channel_origin) = ?1 AND lower(content) = lower(?2)",
            params![clean_chan, trimmed],
        )?;
        Ok(deleted)
    }

    pub fn prune_old_tts_logs(&self, channel: &str, keep_newest: usize) -> Result<Vec<String>> {
        let clean_chan = channel.trim().trim_start_matches('#').to_lowercase();
        let keep = keep_newest.max(10) as i64;
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, file_path FROM tts_logs
             WHERE channel_name = ?1
               AND id NOT IN (
                 SELECT id FROM tts_logs WHERE channel_name = ?1 ORDER BY id DESC LIMIT ?2
               )",
        )?;
        let rows = stmt.query_map(params![clean_chan, keep], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))
        })?;
        let mut old_paths = Vec::new();
        let mut old_ids = Vec::new();
        for r in rows.flatten() {
            old_ids.push(r.0);
            old_paths.push(r.1);
        }
        drop(stmt);
        for id in old_ids {
            let _ = conn.execute("DELETE FROM tts_logs WHERE id = ?1", params![id]);
        }
        Ok(old_paths)
    }

    pub fn delete_user(&self, username: &str) -> Result<bool> {
        let clean = username.trim().to_lowercase();
        let conn = self.conn.lock().unwrap();
        let n = conn.execute("DELETE FROM users WHERE username = ?1", params![clean])?;
        Ok(n > 0)
    }

    // ─── BARK TTS AUDIO LOGS ───

    pub fn insert_tts_log(
        &self,
        channel_name: &str,
        voice_preset: &str,
        text_content: &str,
        file_path: &str,
        duration_secs: f64,
    ) -> Result<TtsLogRow> {
        let now = chrono::Utc::now().timestamp();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO tts_logs (channel_name, voice_preset, text_content, file_path, duration_secs, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                channel_name,
                voice_preset,
                text_content,
                file_path,
                duration_secs,
                now
            ],
        )?;
        let id = conn.last_insert_rowid();
        Ok(TtsLogRow {
            id,
            channel_name: channel_name.to_string(),
            voice_preset: voice_preset.to_string(),
            text_content: text_content.to_string(),
            file_path: file_path.to_string(),
            duration_secs,
            created_at: now,
        })
    }

    pub fn list_tts_logs(
        &self,
        channel_filter: Option<&str>,
        limit: usize,
    ) -> Result<Vec<TtsLogRow>> {
        let conn = self.conn.lock().unwrap();
        let lim = limit.clamp(1, 200) as i64;
        let mut out = Vec::new();
        if let Some(ch) = channel_filter.filter(|s| !s.is_empty() && *s != "*") {
            let clean = ch.trim().trim_start_matches('#').to_lowercase();
            let mut stmt = conn.prepare(
                "SELECT id, channel_name, voice_preset, text_content, file_path, duration_secs, created_at
                 FROM tts_logs WHERE channel_name = ?1 ORDER BY id DESC LIMIT ?2",
            )?;
            let rows = stmt.query_map(params![clean, lim], |r| {
                Ok(TtsLogRow {
                    id: r.get(0)?,
                    channel_name: r.get(1)?,
                    voice_preset: r.get(2)?,
                    text_content: r.get(3)?,
                    file_path: r.get(4)?,
                    duration_secs: r.get(5)?,
                    created_at: r.get(6)?,
                })
            })?;
            for r in rows {
                out.push(r?);
            }
        } else {
            let mut stmt = conn.prepare(
                "SELECT id, channel_name, voice_preset, text_content, file_path, duration_secs, created_at
                 FROM tts_logs ORDER BY id DESC LIMIT ?1",
            )?;
            let rows = stmt.query_map(params![lim], |r| {
                Ok(TtsLogRow {
                    id: r.get(0)?,
                    channel_name: r.get(1)?,
                    voice_preset: r.get(2)?,
                    text_content: r.get(3)?,
                    file_path: r.get(4)?,
                    duration_secs: r.get(5)?,
                    created_at: r.get(6)?,
                })
            })?;
            for r in rows {
                out.push(r?);
            }
        }
        Ok(out)
    }

    pub fn get_tts_log_by_id(&self, id: i64) -> Result<Option<TtsLogRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, channel_name, voice_preset, text_content, file_path, duration_secs, created_at
             FROM tts_logs WHERE id = ?1",
        )?;
        stmt.query_row(params![id], |r| {
            Ok(TtsLogRow {
                id: r.get(0)?,
                channel_name: r.get(1)?,
                voice_preset: r.get(2)?,
                text_content: r.get(3)?,
                file_path: r.get(4)?,
                duration_secs: r.get(5)?,
                created_at: r.get(6)?,
            })
        })
        .optional()
        .map_err(Into::into)
    }

    pub fn delete_tts_log(&self, id: i64) -> Result<Option<TtsLogRow>> {
        let row_opt = self.get_tts_log_by_id(id)?;
        if row_opt.is_some() {
            let conn = self.conn.lock().unwrap();
            conn.execute("DELETE FROM tts_logs WHERE id = ?1", params![id])?;
        }
        Ok(row_opt)
    }

    pub fn import_legacy_tts_logs(&self, legacy_db_path: &Path) -> Result<usize> {
        let count: i64 = {
            let conn = self.conn.lock().unwrap();
            conn.query_row("SELECT COUNT(*) FROM tts_logs", [], |r| r.get(0))?
        };
        if count > 0 || !legacy_db_path.exists() {
            return Ok(0);
        }
        let mut imported = 0usize;
        if let Ok(legacy_conn) = Connection::open(legacy_db_path) {
            if let Ok(mut stmt) = legacy_conn.prepare(
                "SELECT channel, voice_preset, message, file_path FROM tts_logs ORDER BY rowid ASC",
            ) {
                if let Ok(rows) = stmt.query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, Option<String>>(1)?
                            .unwrap_or_else(|| "v2/en_speaker_5".to_string()),
                        r.get::<_, String>(2)?,
                        r.get::<_, String>(3)?,
                    ))
                }) {
                    for row in rows.flatten() {
                        let (chan, voice, msg, path) = row;
                        let clean_chan = chan.trim().trim_start_matches('#').to_lowercase();
                        let clean_path = path.trim_start_matches("static/").to_string();
                        if Path::new("/home/matt/ansv2").join(&clean_path).exists() {
                            let _ = self.insert_tts_log(&clean_chan, &voice, &msg, &clean_path, 0.0);
                            imported += 1;
                        }
                    }
                }
            }
        }
        Ok(imported)
    }
}
