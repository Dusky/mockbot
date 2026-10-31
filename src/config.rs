use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub twitch: TwitchConfig,
    pub web: WebConfig,
    pub markov: MarkovDefaults,
    pub storage: StorageConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TwitchConfig {
    pub nickname: String,
    pub oauth_token: String,
    #[serde(default = "default_mockbot_nick")]
    pub mockbot_nickname: String,
    #[serde(default)]
    pub mockbot_oauth_token: String,
    #[serde(default = "default_mockbot_prefix")]
    pub mockbot_command_prefix: String,
    pub client_id: String,
    #[serde(default)]
    pub client_secret: String,
    #[serde(default)]
    pub oauth_redirect_uri: String,
    pub owner: String,
    pub command_prefix: String,
    pub initial_channels: Vec<String>,
    pub default_ignored_bots: Vec<String>,
}

fn default_mockbot_nick() -> String {
    "mockbot".to_string()
}
fn default_mockbot_prefix() -> String {
    "!mockbot".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WebConfig {
    pub bind_addr: String,
    pub admin_token: String,
    pub public_join_enabled: bool,
    pub public_join_contribute_global: bool,
    pub max_public_channels: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarkovDefaults {
    pub default_order: usize,
    pub max_overlap_ratio: f64,
    pub max_overlap_total: usize,
    pub max_attempts: usize,
    pub min_words: usize,
    pub max_words: usize,
    pub strip_urls: bool,
    pub badwords_file: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StorageConfig {
    pub db_path: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            twitch: TwitchConfig {
                nickname: "anormalskipviewer".to_string(),
                oauth_token: "oauth:your_token_here".to_string(),
                mockbot_nickname: "mockbot".to_string(),
                mockbot_oauth_token: String::new(),
                mockbot_command_prefix: "!mockbot".to_string(),
                client_id: String::new(),
                client_secret: String::new(),
                oauth_redirect_uri: String::new(),
                owner: "firestarman".to_string(),
                command_prefix: "!ansv".to_string(),
                initial_channels: vec!["firestarman".to_string()],
                default_ignored_bots: vec![
                    "nightbot".to_string(),
                    "streamelements".to_string(),
                    "moobot".to_string(),
                    "fossabot".to_string(),
                    "wizebot".to_string(),
                    "sery_bot".to_string(),
                    "soundalerts".to_string(),
                    "streamlabs".to_string(),
                    "pokemoncommunitygame".to_string(),
                    "kofistreambot".to_string(),
                    "commanderroot".to_string(),
                ],
            },
            web: WebConfig {
                bind_addr: "0.0.0.0:5002".to_string(),
                admin_token: "ansv2-admin".to_string(),
                public_join_enabled: true,
                public_join_contribute_global: false,
                max_public_channels: 250,
            },
            markov: MarkovDefaults {
                default_order: 2,
                max_overlap_ratio: 0.70,
                max_overlap_total: 15,
                max_attempts: 30,
                min_words: 3,
                max_words: 35,
                strip_urls: true,
                badwords_file: "badwords.txt".to_string(),
            },
            storage: StorageConfig {
                db_path: "ansv2.db".to_string(),
            },
        }
    }
}

impl AppConfig {
    pub fn load_or_create(path: &str) -> Result<Self> {
        if Path::new(path).exists() {
            let raw = fs::read_to_string(path)
                .with_context(|| format!("Failed to read config file {}", path))?;
            let cfg: AppConfig = toml::from_str(&raw)
                .with_context(|| format!("Failed to parse TOML in {}", path))?;
            return Ok(cfg);
        }

        // Try auto-migrating from legacy settings.conf if available
        let mut cfg = Self::default();
        let legacy_paths = [
            "/home/matt/ansv/ansv-bot/settings.conf",
            "/home/matt/ansv/settings.conf",
            "/home/matt/ansv-bot/settings.conf",
        ];
        for legacy in legacy_paths {
            if Path::new(legacy).exists() {
                if let Ok(text) = fs::read_to_string(legacy) {
                    Self::apply_legacy_ini(&mut cfg, &text);
                    break;
                }
            }
        }

        let serialized = toml::to_string_pretty(&cfg)?;
        fs::write(path, &serialized)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
        }
        Ok(cfg)
    }

    pub fn apply_legacy_ini(cfg: &mut AppConfig, ini_text: &str) {
        for line in ini_text.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with(';') || trimmed.starts_with('[') {
                continue;
            }
            if let Some((k, v)) = trimmed.split_once('=') {
                let key = k.trim().to_lowercase();
                let val = v.trim();
                match key.as_str() {
                    "tmi_token" => cfg.twitch.oauth_token = val.to_string(),
                    "client_id" => cfg.twitch.client_id = val.to_string(),
                    "nickname" => cfg.twitch.nickname = val.to_lowercase(),
                    "owner" => cfg.twitch.owner = val.to_lowercase(),
                    "channels" => {
                        let chans: Vec<String> = val
                            .split(',')
                            .map(|s| s.trim().trim_start_matches('#').to_lowercase())
                            .filter(|s| !s.is_empty())
                            .collect();
                        if !chans.is_empty() {
                            cfg.twitch.initial_channels = chans;
                        }
                    }
                    "admin_password" => {
                        if !val.is_empty() {
                            cfg.web.admin_token = val.to_string();
                        }
                    }
                    _ => {}
                }
            }
        }
    }
}
