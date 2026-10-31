pub mod brain;
pub mod filter;

pub use brain::{BrainKind, BrainStats, MarkovBrain, TokenId, Vocabulary, END_TOKEN, START_TOKEN};
pub use filter::MessageFilter;

use rand::seq::SliceRandom;
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrainWeight {
    pub brain: String,
    pub weight: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenerationParams {
    pub brains: Vec<BrainWeight>,
    pub order: usize,
    pub temperature: f64,
    pub seed: Option<String>,
    pub min_words: usize,
    pub max_words: usize,
    pub max_chars: usize,
    pub max_overlap_ratio: f64,
    pub max_overlap_total: usize,
    pub max_attempts: usize,
    #[serde(default)]
    pub recent_outputs: Vec<String>,
    #[serde(default)]
    pub blocked_words: Vec<String>,
}

impl Default for GenerationParams {
    fn default() -> Self {
        Self {
            brains: vec![BrainWeight {
                brain: "global".to_string(),
                weight: 1.0,
            }],
            order: 2,
            temperature: 1.0,
            seed: None,
            min_words: 3,
            max_words: 35,
            max_chars: 450,
            max_overlap_ratio: 0.70,
            max_overlap_total: 15,
            max_attempts: 35,
            recent_outputs: Vec::new(),
            blocked_words: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenerationResult {
    pub text: String,
    pub attempts_used: usize,
    pub brains_used: Vec<String>,
    pub seeded: bool,
    pub word_count: usize,
}

pub struct MarkovEngine {
    pub vocab: Vocabulary,
    pub brains: HashMap<String, MarkovBrain>,
    pub filter: MessageFilter,
}

impl MarkovEngine {
    pub fn new(filter: MessageFilter) -> Self {
        let mut brains = HashMap::new();
        brains.insert(
            "global".to_string(),
            MarkovBrain::new("global", BrainKind::Global, "Global multi-channel corpus"),
        );
        Self {
            vocab: Vocabulary::new(),
            brains,
            filter,
        }
    }

    pub fn ensure_brain(&mut self, name: &str, kind: BrainKind, description: &str) {
        if !self.brains.contains_key(name) {
            self.brains
                .insert(name.to_string(), MarkovBrain::new(name, kind, description));
        }
    }

    pub fn channel_brain_name(channel: &str) -> String {
        let clean = channel.trim().trim_start_matches('#').to_lowercase();
        format!("channel:{}", clean)
    }

    /// Tokenize and train a raw sentence into the specified target brains.
    /// Returns the cleaned sentence if it passed sanitization and was trained into at least one brain.
    pub fn learn_sentence(&mut self, raw: &str, target_brains: &[String]) -> Option<String> {
        let cleaned = self.filter.sanitize_for_training(raw)?;
        self.learn_sanitized(&cleaned, target_brains);
        Some(cleaned)
    }

    pub fn learn_sanitized(&mut self, cleaned: &str, target_brains: &[String]) -> bool {
        let words: Vec<&str> = cleaned.split_whitespace().collect();
        if words.len() < 2 {
            return false;
        }
        let tokens: Vec<TokenId> = words.iter().map(|w| self.vocab.intern(w)).collect();
        let mut added_any = false;

        for bname in target_brains {
            if !self.brains.contains_key(bname) {
                let kind = if bname == "global" {
                    BrainKind::Global
                } else if bname.starts_with("channel:") {
                    BrainKind::Channel
                } else {
                    BrainKind::Custom
                };
                self.brains
                    .insert(bname.clone(), MarkovBrain::new(bname.clone(), kind, ""));
            }
            if let Some(brain) = self.brains.get_mut(bname) {
                if brain.add_tokens(&tokens) {
                    added_any = true;
                }
            }
        }
        added_any
    }

    pub fn list_brain_stats(&self) -> Vec<BrainStats> {
        let mut list: Vec<BrainStats> = self.brains.values().map(|b| b.stats()).collect();
        list.sort_by(|a, b| {
            let rank_a = match a.kind {
                BrainKind::Global => 0,
                BrainKind::Custom => 1,
                BrainKind::Channel => 2,
            };
            let rank_b = match b.kind {
                BrainKind::Global => 0,
                BrainKind::Custom => 1,
                BrainKind::Channel => 2,
            };
            rank_a
                .cmp(&rank_b)
                .then_with(|| b.sentence_count.cmp(&a.sentence_count))
                .then_with(|| a.name.cmp(&b.name))
        });
        list
    }

    /// Generate a response using weighted multi-brain step-level blending, variable-order backoff,
    /// bidirectional seed biasing, and markovify-grade overlap filtering.
    pub fn generate(&self, params: &GenerationParams) -> Option<GenerationResult> {
        let mut active: Vec<(&MarkovBrain, f64)> = Vec::new();
        for bw in &params.brains {
            if bw.weight <= 0.0 {
                continue;
            }
            if let Some(b) = self.brains.get(&bw.brain) {
                if b.sentence_count > 0 {
                    active.push((b, bw.weight));
                }
            }
        }

        // Automatic fallback to global brain if requested channel brain(s) are empty
        if active.is_empty() {
            if let Some(global) = self.brains.get("global") {
                if global.sentence_count > 0 {
                    active.push((global, 1.0));
                }
            }
        }
        if active.is_empty() {
            return None;
        }

        let mut rng = rand::thread_rng();
        let order = params.order.clamp(1, 3);
        let temp = params.temperature.clamp(0.25, 3.0);

        // Extract seed token candidates if seed phrase is provided
        let seed_tokens: Vec<TokenId> = params
            .seed
            .as_ref()
            .map(|s| self.extract_seed_tokens(s, &active))
            .unwrap_or_default();

        let brains_used: Vec<String> = active.iter().map(|(b, _)| b.name.clone()).collect();
        let max_attempts = params.max_attempts.max(10);

        // Pass 1: Strict overlap check + min_words check
        // Pass 2: Relaxed overlap check if corpus is tiny
        for relax_pass in 0..2 {
            let attempts = if relax_pass == 0 {
                max_attempts
            } else {
                max_attempts / 2
            };
            for attempt in 1..=attempts {
                let mut used_seed = false;
                let candidate_opt = if !seed_tokens.is_empty() && attempt <= (attempts * 3 / 4) {
                    if let Some(toks) = self.generate_seeded_tokens(
                        &active,
                        &seed_tokens,
                        order,
                        temp,
                        params.max_words,
                        &mut rng,
                    ) {
                        used_seed = true;
                        Some(toks)
                    } else {
                        self.generate_forward_tokens(
                            &active,
                            order,
                            temp,
                            params.max_words,
                            &mut rng,
                        )
                    }
                } else {
                    self.generate_forward_tokens(&active, order, temp, params.max_words, &mut rng)
                };

                let Some(tokens) = candidate_opt else {
                    continue;
                };

                if relax_pass == 0 && tokens.len() < params.min_words {
                    continue;
                }
                if tokens.len() < 2 || tokens.len() > params.max_words {
                    continue;
                }

                // Check overlap against all active brains on pass 0
                if relax_pass == 0 {
                    let passes_all = active.iter().all(|(brain, _)| {
                        // Only enforce strict overlap if brain has >= 25 sentences
                        if brain.sentence_count < 25 {
                            true
                        } else {
                            brain.passes_overlap_check(
                                &tokens,
                                params.max_overlap_ratio,
                                params.max_overlap_total,
                            )
                        }
                    });
                    if !passes_all {
                        continue;
                    }
                }

                let text = self.detokenize(&tokens);
                if text.is_empty() || text.len() > params.max_chars || !self.filter.is_safe(&text) {
                    continue;
                }
                if !params.blocked_words.is_empty() {
                    let lower_text = text.to_lowercase();
                    if params
                        .blocked_words
                        .iter()
                        .any(|bw| !bw.is_empty() && lower_text.contains(bw))
                    {
                        continue;
                    }
                }
                if params
                    .recent_outputs
                    .iter()
                    .any(|prev| prev.eq_ignore_ascii_case(&text))
                {
                    continue;
                }

                return Some(GenerationResult {
                    text,
                    attempts_used: attempt + relax_pass * max_attempts,
                    brains_used,
                    seeded: used_seed,
                    word_count: tokens.len(),
                });
            }
        }

        None
    }

    fn extract_seed_tokens(&self, seed_text: &str, active: &[(&MarkovBrain, f64)]) -> Vec<TokenId> {
        let mut candidates = Vec::new();
        // Prefer longer/more specific words from the seed phrase
        let mut words: Vec<&str> = seed_text
            .split_whitespace()
            .map(|w| w.trim_matches(|c: char| !c.is_alphanumeric()))
            .filter(|w| w.len() >= 2)
            .collect();
        words.sort_by_key(|w| std::cmp::Reverse(w.len()));

        for w in words {
            for &id in self.vocab.lookup_ci(w) {
                if active.iter().any(|(b, _)| b.unique_tokens.contains(&id)) && !candidates.contains(&id) {
                    candidates.push(id);
                }
            }
        }
        candidates
    }

    /// Bidirectional seeded generation: picks an Order-2 state `(w1, w2)` containing a seed token,
    /// walks backward via `rev_chains_2` to `START_TOKEN`, then walks forward to `END_TOKEN`.
    fn generate_seeded_tokens<R: Rng>(
        &self,
        active: &[(&MarkovBrain, f64)],
        seed_tokens: &[TokenId],
        order: usize,
        temp: f64,
        max_words: usize,
        rng: &mut R,
    ) -> Option<Vec<TokenId>> {
        let &seed_tok = seed_tokens.choose(rng)?;

        // Gather all (w1, w2) states starting with seed_tok across active brains
        let mut possible_states: Vec<((TokenId, TokenId), f64)> = Vec::new();
        for &(brain, weight) in active {
            if let Some(states) = brain.token_states_2.get(&seed_tok) {
                let scale = weight / (states.len() as f64).max(1.0);
                for &st in states {
                    possible_states.push((st, scale));
                }
            }
        }
        if possible_states.is_empty() {
            return None;
        }

        let total_w: f64 = possible_states.iter().map(|(_, w)| *w).sum();
        if total_w <= 0.0 {
            return None;
        }
        let mut roll = rng.gen_range(0.0..total_w);
        let mut chosen_state = possible_states[0].0;
        for &(st, w) in &possible_states {
            if roll <= w {
                chosen_state = st;
                break;
            }
            roll -= w;
        }

        // Walk backward from chosen_state `(w1, w2)` up to `START_TOKEN`
        let mut prefix: Vec<TokenId> = Vec::new();
        let mut cur_back = chosen_state;
        let max_back = (max_words / 2).max(6);
        for _ in 0..max_back {
            let prev = self.sample_prev_order2(active, cur_back, temp, rng)?;
            if prev == START_TOKEN {
                break;
            }
            prefix.push(prev);
            cur_back = (prev, cur_back.0);
        }
        prefix.reverse();

        let mut result = prefix;
        result.push(chosen_state.0);
        result.push(chosen_state.1);

        // Walk forward to END_TOKEN
        while result.len() < max_words {
            let next = self.sample_next_with_backoff(active, &result, order, temp, rng)?;
            if next == END_TOKEN {
                break;
            }
            result.push(next);
        }

        Some(result)
    }

    fn generate_forward_tokens<R: Rng>(
        &self,
        active: &[(&MarkovBrain, f64)],
        order: usize,
        temp: f64,
        max_words: usize,
        rng: &mut R,
    ) -> Option<Vec<TokenId>> {
        // Choose a starter state by sampling one of the active brains according to weight
        let starter_brain = Self::pick_weighted_brain(active, rng)?;

        let mut tokens: Vec<TokenId> = Vec::with_capacity(16);
        if order >= 3 {
            if let Some((w1, w2, w3)) = starter_brain.sample_starter_3(rng) {
                tokens.push(w1);
                tokens.push(w2);
                tokens.push(w3);
            }
        }
        if tokens.is_empty() && order >= 2 {
            if let Some((w1, w2)) = starter_brain.sample_starter_2(rng) {
                tokens.push(w1);
                tokens.push(w2);
            }
        }
        if tokens.is_empty() {
            let w1 = starter_brain.sample_starter_1(rng)?;
            tokens.push(w1);
        }

        while tokens.len() < max_words {
            let Some(next) = self.sample_next_with_backoff(active, &tokens, order, temp, rng) else {
                break;
            };
            if next == END_TOKEN {
                break;
            }
            tokens.push(next);
        }

        Some(tokens)
    }

    fn pick_weighted_brain<'a, R: Rng>(
        active: &[(&'a MarkovBrain, f64)],
        rng: &mut R,
    ) -> Option<&'a MarkovBrain> {
        let total: f64 = active.iter().map(|(_, w)| *w).sum();
        if total <= 0.0 {
            return active.first().map(|(b, _)| *b);
        }
        let mut roll = rng.gen_range(0.0..total);
        for &(b, w) in active {
            if roll <= w {
                return Some(b);
            }
            roll -= w;
        }
        active.first().map(|(b, _)| *b)
    }

    /// Samples the next token using normalized step-level probability blending across all active brains,
    /// with automatic backoff from Order 3 -> Order 2 -> Order 1 and temperature scaling.
    fn sample_next_with_backoff<R: Rng>(
        &self,
        active: &[(&MarkovBrain, f64)],
        history: &[TokenId],
        max_order: usize,
        temp: f64,
        rng: &mut R,
    ) -> Option<TokenId> {
        if max_order >= 3 && history.len() >= 3 {
            let n = history.len();
            let s3 = (history[n - 3], history[n - 2], history[n - 1]);
            if let Some(tok) = Self::sample_blended_transitions(
                active,
                |b| b.chains_3.get(&s3).map(|v| v.as_slice()),
                temp,
                rng,
            ) {
                return Some(tok);
            }
        }

        if max_order >= 2 && history.len() >= 2 {
            let n = history.len();
            let s2 = (history[n - 2], history[n - 1]);
            if let Some(tok) = Self::sample_blended_transitions(
                active,
                |b| b.chains_2.get(&s2).map(|v| v.as_slice()),
                temp,
                rng,
            ) {
                return Some(tok);
            }
        }

        if let Some(&last) = history.last() {
            if let Some(tok) = Self::sample_blended_transitions(
                active,
                |b| b.chains_1.get(&last).map(|v| v.as_slice()),
                temp,
                rng,
            ) {
                return Some(tok);
            }
        }

        None
    }

    fn sample_prev_order2<R: Rng>(
        &self,
        active: &[(&MarkovBrain, f64)],
        state: (TokenId, TokenId),
        temp: f64,
        rng: &mut R,
    ) -> Option<TokenId> {
        Self::sample_blended_transitions(
            active,
            |b| b.rev_chains_2.get(&state).map(|v| v.as_slice()),
            temp,
            rng,
        )
    }

    fn sample_blended_transitions<'a, F, R>(
        active: &[(&'a MarkovBrain, f64)],
        getter: F,
        temp: f64,
        rng: &mut R,
    ) -> Option<TokenId>
    where
        F: Fn(&'a MarkovBrain) -> Option<&'a [(TokenId, u32)]>,
        R: Rng,
    {
        let mut matching_brains: Vec<(&[(TokenId, u32)], u64, f64)> = Vec::new();
        let mut total_brain_weight = 0.0;

        for &(brain, weight) in active {
            if let Some(slice) = getter(brain) {
                if !slice.is_empty() {
                    let sum_counts: u64 = slice.iter().map(|(_, c)| *c as u64).sum();
                    if sum_counts > 0 {
                        matching_brains.push((slice, sum_counts, weight));
                        total_brain_weight += weight;
                    }
                }
            }
        }

        if matching_brains.is_empty() || total_brain_weight <= 0.0 {
            return None;
        }

        let mut brain_roll = rng.gen_range(0.0..total_brain_weight);
        let mut chosen = matching_brains[0];
        for entry in matching_brains {
            if brain_roll <= entry.2 {
                chosen = entry;
                break;
            }
            brain_roll -= entry.2;
        }

        let (slice, sum_counts, _) = chosen;
        // Fast integer path when temperature is ~1.0
        if (temp - 1.0).abs() < 0.05 || slice.len() == 1 {
            let mut tok_roll = rng.gen_range(0..sum_counts);
            for &(tok, c) in slice {
                if tok_roll < c as u64 {
                    return Some(tok);
                }
                tok_roll -= c as u64;
            }
            return Some(slice[0].0);
        }

        // Temperature-scaled sampling: weight = count^(1.0 / temp)
        let inv_temp = 1.0 / temp.clamp(0.25, 3.0);
        let total_scaled: f64 = slice.iter().map(|(_, c)| (*c as f64).powf(inv_temp)).sum();
        if total_scaled <= 0.0 {
            return Some(slice[0].0);
        }
        let mut roll = rng.gen_range(0.0..total_scaled);
        for &(tok, c) in slice {
            let w = (c as f64).powf(inv_temp);
            if roll <= w {
                return Some(tok);
            }
            roll -= w;
        }
        Some(slice[0].0)
    }

    pub fn detokenize(&self, tokens: &[TokenId]) -> String {
        let mut out = String::with_capacity(tokens.len() * 6);
        for (i, &t) in tokens.iter().enumerate() {
            if i > 0 {
                out.push(' ');
            }
            out.push_str(self.vocab.resolve(t));
        }
        out
    }
}
