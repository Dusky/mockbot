use rand::Rng;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};

pub type TokenId = u32;
pub const END_TOKEN: TokenId = 0;
pub const START_TOKEN: TokenId = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BrainKind {
    Global,
    Channel,
    Custom,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrainStats {
    pub name: String,
    pub kind: BrainKind,
    pub description: String,
    #[serde(default)]
    pub default_voice: String,
    pub sentence_count: usize,
    pub total_tokens: usize,
    pub unique_tokens: usize,
    pub states_order_1: usize,
    pub states_order_2: usize,
    pub states_order_3: usize,
    pub last_updated: i64,
}

#[derive(Debug, Default, Clone)]
pub struct Vocabulary {
    id_to_word: Vec<String>,
    word_to_id: HashMap<String, TokenId>,
    lower_to_ids: HashMap<String, Vec<TokenId>>,
}

impl Vocabulary {
    pub fn new() -> Self {
        let mut v = Self {
            id_to_word: Vec::with_capacity(16384),
            word_to_id: HashMap::with_capacity(16384),
            lower_to_ids: HashMap::with_capacity(16384),
        };
        // Reserve 0 = END_TOKEN, 1 = START_TOKEN
        v.id_to_word.push("<END>".to_string());
        v.id_to_word.push("<START>".to_string());
        v
    }

    pub fn intern(&mut self, word: &str) -> TokenId {
        if let Some(&id) = self.word_to_id.get(word) {
            return id;
        }
        let id = self.id_to_word.len() as TokenId;
        let owned = word.to_string();
        self.id_to_word.push(owned.clone());
        self.word_to_id.insert(owned, id);
        self.lower_to_ids
            .entry(word.to_lowercase())
            .or_default()
            .push(id);
        id
    }

    pub fn resolve(&self, id: TokenId) -> &str {
        self.id_to_word
            .get(id as usize)
            .map(|s| s.as_str())
            .unwrap_or("")
    }

    pub fn lookup_ci(&self, word: &str) -> &[TokenId] {
        let lower = word.trim().to_lowercase();
        self.lower_to_ids
            .get(&lower)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    pub fn len(&self) -> usize {
        self.id_to_word.len().saturating_sub(2)
    }
}

#[inline]
fn add_transition(vec: &mut Vec<(TokenId, u32)>, next: TokenId, delta: u32) {
    for (tok, count) in vec.iter_mut() {
        if *tok == next {
            *count = count.saturating_add(delta);
            return;
        }
    }
    vec.push((next, delta));
}

#[inline]
pub fn hash_tokens(tokens: &[TokenId]) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    tokens.hash(&mut hasher);
    hasher.finish()
}

#[derive(Debug, Clone)]
pub struct MarkovBrain {
    pub name: String,
    pub kind: BrainKind,
    pub description: String,
    pub default_voice: String,
    pub sentence_count: usize,
    pub total_tokens: usize,
    pub last_updated: i64,

    pub unique_tokens: HashSet<TokenId>,

    // Starters for Order 1, 2, 3 (stored per-sentence for O(1) insert and O(1) weighted random pick)
    pub starters_1: Vec<TokenId>,
    pub starters_2: Vec<(TokenId, TokenId)>,
    pub starters_3: Vec<(TokenId, TokenId, TokenId)>,

    // Forward transition tables for Order 1, 2, 3
    pub chains_1: HashMap<TokenId, Vec<(TokenId, u32)>>,
    pub chains_2: HashMap<(TokenId, TokenId), Vec<(TokenId, u32)>>,
    pub chains_3: HashMap<(TokenId, TokenId, TokenId), Vec<(TokenId, u32)>>,

    // Reverse Order-2 transitions: given (w1, w2), what preceded w1? (including START_TOKEN)
    pub rev_chains_2: HashMap<(TokenId, TokenId), Vec<(TokenId, u32)>>,

    // Index mapping token -> list of (token, next_token) states for bidirectional seed generation
    pub token_states_2: HashMap<TokenId, Vec<(TokenId, TokenId)>>,

    // Exact sentence hashes to prevent verbatim copies
    pub exact_hashes: HashSet<u64>,

    // Flat token buffer separated by END_TOKEN (0) + index of (w1, w2) -> positions for overlap checks
    pub corpus_tokens: Vec<TokenId>,
    pub bigram_positions: HashMap<(TokenId, TokenId), Vec<u32>>,
}

impl MarkovBrain {
    pub fn new(name: impl Into<String>, kind: BrainKind, description: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            kind,
            description: description.into(),
            default_voice: String::new(),
            sentence_count: 0,
            total_tokens: 0,
            last_updated: chrono::Utc::now().timestamp(),
            unique_tokens: HashSet::new(),
            starters_1: Vec::new(),
            starters_2: Vec::new(),
            starters_3: Vec::new(),
            chains_1: HashMap::new(),
            chains_2: HashMap::new(),
            chains_3: HashMap::new(),
            rev_chains_2: HashMap::new(),
            token_states_2: HashMap::new(),
            exact_hashes: HashSet::new(),
            corpus_tokens: Vec::new(),
            bigram_positions: HashMap::new(),
        }
    }

    pub fn clear(&mut self) {
        self.sentence_count = 0;
        self.total_tokens = 0;
        self.last_updated = chrono::Utc::now().timestamp();
        self.unique_tokens.clear();
        self.starters_1.clear();
        self.starters_2.clear();
        self.starters_3.clear();
        self.chains_1.clear();
        self.chains_2.clear();
        self.chains_3.clear();
        self.rev_chains_2.clear();
        self.token_states_2.clear();
        self.exact_hashes.clear();
        self.corpus_tokens.clear();
        self.bigram_positions.clear();
    }

    pub fn stats(&self) -> BrainStats {
        BrainStats {
            name: self.name.clone(),
            kind: self.kind,
            description: self.description.clone(),
            default_voice: self.default_voice.clone(),
            sentence_count: self.sentence_count,
            total_tokens: self.total_tokens,
            unique_tokens: self.unique_tokens.len(),
            states_order_1: self.chains_1.len(),
            states_order_2: self.chains_2.len(),
            states_order_3: self.chains_3.len(),
            last_updated: self.last_updated,
        }
    }

    /// Train a tokenized sentence into this brain. Returns true if added (not a duplicate).
    pub fn add_tokens(&mut self, tokens: &[TokenId]) -> bool {
        if tokens.len() < 2 {
            return false;
        }
        let h = hash_tokens(tokens);
        if !self.exact_hashes.insert(h) {
            // Already trained this exact sentence in this brain; still bump starter/chain weights lightly or skip
            return false;
        }

        self.sentence_count += 1;
        self.total_tokens += tokens.len();
        self.last_updated = chrono::Utc::now().timestamp();

        let base_pos = self.corpus_tokens.len() as u32;
        for (i, &t) in tokens.iter().enumerate() {
            self.unique_tokens.insert(t);
            self.corpus_tokens.push(t);
            if i + 1 < tokens.len() {
                let bg = (t, tokens[i + 1]);
                let pos_list = self.bigram_positions.entry(bg).or_default();
                if pos_list.len() < 64 {
                    pos_list.push(base_pos + i as u32);
                }
            }
        }
        self.corpus_tokens.push(END_TOKEN);

        // Starters (O(1) push, frequency-weighted naturally by occurrence)
        self.starters_1.push(tokens[0]);
        self.starters_2.push((tokens[0], tokens[1]));
        if tokens.len() >= 3 {
            self.starters_3.push((tokens[0], tokens[1], tokens[2]));
        }

        // Order 1 transitions
        for i in 0..tokens.len() {
            let curr = tokens[i];
            let next = if i + 1 < tokens.len() {
                tokens[i + 1]
            } else {
                END_TOKEN
            };
            add_transition(self.chains_1.entry(curr).or_default(), next, 1);
        }

        // Order 2 transitions + Reverse Order 2 transitions + Token state index
        for i in 0..(tokens.len() - 1) {
            let w1 = tokens[i];
            let w2 = tokens[i + 1];
            let next = if i + 2 < tokens.len() {
                tokens[i + 2]
            } else {
                END_TOKEN
            };
            let state = (w1, w2);
            add_transition(self.chains_2.entry(state).or_default(), next, 1);

            let prev = if i == 0 { START_TOKEN } else { tokens[i - 1] };
            add_transition(self.rev_chains_2.entry(state).or_default(), prev, 1);

            let states_for_w1 = self.token_states_2.entry(w1).or_default();
            if states_for_w1.len() < 128 && !states_for_w1.contains(&state) {
                states_for_w1.push(state);
            }
        }

        // Order 3 transitions
        if tokens.len() >= 3 {
            for i in 0..(tokens.len() - 2) {
                let state = (tokens[i], tokens[i + 1], tokens[i + 2]);
                let next = if i + 3 < tokens.len() {
                    tokens[i + 3]
                } else {
                    END_TOKEN
                };
                add_transition(self.chains_3.entry(state).or_default(), next, 1);
            }
        }

        true
    }

    /// Checks whether `candidate` violates exact-match or contiguous overlap limits against this brain.
    /// Equivalent to `markovify.Text.test_sentence_output(words, max_overlap_ratio, max_overlap_total)`.
    pub fn passes_overlap_check(
        &self,
        candidate: &[TokenId],
        max_overlap_ratio: f64,
        max_overlap_total: usize,
    ) -> bool {
        let h = hash_tokens(candidate);
        if self.exact_hashes.contains(&h) {
            return false;
        }
        if candidate.len() < 4 {
            return true;
        }

        let overlap_ratio = ((candidate.len() as f64) * max_overlap_ratio).round() as usize;
        let overlap_max = overlap_ratio.min(max_overlap_total);
        let overlap_over = (overlap_max + 1).max(4);

        if candidate.len() < overlap_over {
            return true;
        }

        let gram_count = (candidate.len() - overlap_over) + 1;
        for i in 0..gram_count {
            let slice = &candidate[i..(i + overlap_over)];
            let bg = (slice[0], slice[1]);
            if let Some(positions) = self.bigram_positions.get(&bg) {
                for &pos in positions {
                    let start = pos as usize;
                    let end = start + overlap_over;
                    if end <= self.corpus_tokens.len() && &self.corpus_tokens[start..end] == slice {
                        return false;
                    }
                }
            }
        }

        true
    }

    pub fn sample_starter_2<R: Rng>(&self, rng: &mut R) -> Option<(TokenId, TokenId)> {
        if self.starters_2.is_empty() {
            return None;
        }
        Some(self.starters_2[rng.gen_range(0..self.starters_2.len())])
    }

    pub fn sample_starter_3<R: Rng>(&self, rng: &mut R) -> Option<(TokenId, TokenId, TokenId)> {
        if self.starters_3.is_empty() {
            return None;
        }
        Some(self.starters_3[rng.gen_range(0..self.starters_3.len())])
    }

    pub fn sample_starter_1<R: Rng>(&self, rng: &mut R) -> Option<TokenId> {
        if self.starters_1.is_empty() {
            return None;
        }
        Some(self.starters_1[rng.gen_range(0..self.starters_1.len())])
    }
}
