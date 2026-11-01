use regex::Regex;
use std::fs;
use std::path::Path;

pub struct MessageFilter {
    url_regex: Regex,
    pii_regex: Regex,
    badword_regexes: Vec<Regex>,
    blocked_substrings: Vec<String>,
    pub strip_urls: bool,
}

impl MessageFilter {
    pub fn new(strip_urls: bool, badwords_path: Option<&str>) -> Self {
        let url_regex = Regex::new(r"(?i)\b(?:https?://|www\.)\S+").unwrap();
        // Match IPv4 addresses, US/intl phone numbers, or email addresses to prevent doxxing
        let pii_regex = Regex::new(
            r"(?i)(?:\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b|\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b)",
        )
        .unwrap();
        let mut filter = Self {
            url_regex,
            pii_regex,
            badword_regexes: Vec::new(),
            blocked_substrings: vec![
                "second ad break is starting".to_string(),
                "sticking with us through this break".to_string(),
                "subscribing to remove ads".to_string(),
                "could not generate a message".to_string(),
            ],
            strip_urls,
        };
        if let Some(p) = badwords_path {
            filter.load_badwords_file(p);
        }
        // Also check legacy badwords.txt if local doesn't exist yet
        if filter.badword_regexes.is_empty() && filter.blocked_substrings.len() <= 4 {
            let legacy = "/home/matt/ansv/ansv-bot/badwords.txt";
            if Path::new(legacy).exists() {
                filter.load_badwords_file(legacy);
            }
        }
        filter
    }

    pub fn load_badwords_file(&mut self, path: &str) {
        if let Ok(content) = fs::read_to_string(path) {
            for line in content.lines() {
                let trimmed = line.trim();
                if trimmed.is_empty() || trimmed.starts_with('#') {
                    continue;
                }
                // Skip pure URL regex from legacy badwords.txt since we handle URLs explicitly
                if trimmed.contains("https?|ftp|file") {
                    continue;
                }
                if let Ok(re) = Regex::new(&format!("(?i){}", trimmed)) {
                    self.badword_regexes.push(re);
                } else {
                    self.blocked_substrings.push(trimmed.to_lowercase());
                }
            }
        }
    }

    /// Cleans a chat message for training or returns None if it should be ignored.
    pub fn sanitize_for_training(&self, raw: &str) -> Option<String> {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return None;
        }
        // Ignore bot commands and slash commands
        if trimmed.starts_with('!') || trimmed.starts_with('/') || trimmed.starts_with('.') {
            return None;
        }

        let mut cleaned: String = trimmed
            .chars()
            .filter(|c| !c.is_control())
            .collect();

        if cleaned.contains("â€") {
            cleaned = cleaned
                .replace("â€™", "'")
                .replace("â€˜", "'")
                .replace("â€œ", "\"")
                .replace("â€\u{9d}", "\"")
                .replace("â€”", "—")
                .replace("â€“", "-");
        }

        // Strip Twitch /me ACTION wrapper if present
        if let Some(rest) = cleaned.strip_prefix("\u{1}ACTION ") {
            cleaned = rest.trim_end_matches('\u{1}').to_string();
        }

        if self.strip_urls {
            cleaned = self.url_regex.replace_all(&cleaned, "").to_string();
        } else if self.url_regex.is_match(&cleaned) {
            return None;
        }

        // Collapse whitespace (keep @username mentions so Markov can naturally @mention users)
        let words: Vec<&str> = cleaned.split_whitespace().collect();
        if words.len() < 2 {
            return None;
        }
        // Reject repetitive emote/raid-chant spam (e.g. "Foxy Raid egancaRaid ... Foxy Raid egancaRaid ...")
        if words.len() >= 6 {
            let unique_count = words
                .iter()
                .map(|w| w.to_lowercase())
                .collect::<std::collections::HashSet<String>>()
                .len();
            if unique_count * 100 / words.len() < 42 {
                return None;
            }
        }
        let normalized = words.join(" ");

        if !self.is_safe(&normalized) {
            return None;
        }

        Some(normalized)
    }

    pub fn is_safe(&self, text: &str) -> bool {
        // 1. Block PII (IPv4, phone numbers, email addresses)
        if self.pii_regex.is_match(text) {
            return false;
        }

        // 2. Block Zalgo combining-diacritic spam, Braille ASCII art, and box-drawing art
        let mut combining_count = 0usize;
        let mut art_count = 0usize;
        let mut prev_ch = '\0';
        let mut repeat_run = 0usize;
        for ch in text.chars() {
            let code = ch as u32;
            if (0x0300..=0x036F).contains(&code)
                || (0x1AB0..=0x1AFF).contains(&code)
                || (0x20D0..=0x20FF).contains(&code)
            {
                combining_count += 1;
                if combining_count > 3 {
                    return false;
                }
            }
            if (0x2800..=0x28FF).contains(&code) || (0x2500..=0x259F).contains(&code) {
                art_count += 1;
                if art_count > 2 {
                    return false;
                }
            }
            if ch == prev_ch {
                repeat_run += 1;
                if repeat_run >= 16 {
                    return false;
                }
            } else {
                prev_ch = ch;
                repeat_run = 1;
            }
        }

        let lower = text.to_lowercase();
        for sub in &self.blocked_substrings {
            if lower.contains(sub) {
                return false;
            }
        }
        for re in &self.badword_regexes {
            if re.is_match(text) {
                return false;
            }
        }
        true
    }

    /// Ensures outbound bot messages can never execute Twitch slash/dot commands (/ban, /raid, .timeout, !cmd)
    /// and are capped to a safe Twitch IRC length.
    pub fn sanitize_outbound(text: &str) -> String {
        let cleaned: String = text
            .chars()
            .filter(|c| !c.is_control())
            .collect::<String>();
        let safe_start = cleaned
            .trim()
            .trim_start_matches(|c: char| matches!(c, '/' | '.' | '!' | '\\'))
            .trim();
        safe_start.chars().take(450).collect()
    }
}
