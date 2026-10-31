use regex::Regex;
use std::fs;
use std::path::Path;

pub struct MessageFilter {
    url_regex: Regex,
    badword_regexes: Vec<Regex>,
    blocked_substrings: Vec<String>,
    pub strip_urls: bool,
}

impl MessageFilter {
    pub fn new(strip_urls: bool, badwords_path: Option<&str>) -> Self {
        let url_regex = Regex::new(r"(?i)\b(?:https?://|www\.)\S+").unwrap();
        let mut filter = Self {
            url_regex,
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
        let normalized = words.join(" ");

        if !self.is_safe(&normalized) {
            return None;
        }

        Some(normalized)
    }

    pub fn is_safe(&self, text: &str) -> bool {
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
}
