//! Markov-chain text generation — the heart of Mockbot.
//!
//! A Rust port of the Python `MarkovBrain`. It learns word-to-word transitions
//! from training lines and generates new sentences from them. Deliberately
//! dependency-free for now: a tiny seeded PRNG stands in for the `rand` crate so
//! this builds and tests offline.

use std::collections::HashMap;

/// A minimal deterministic PRNG (xorshift64*). Deterministic seeding is what
/// makes generation unit-testable.
pub struct Rng {
    state: u64,
}

impl Rng {
    pub fn new(seed: u64) -> Self {
        // A zero state is a fixed point for xorshift, so steer away from it.
        Rng {
            state: if seed == 0 { 0x9E37_79B9_7F4A_7C15 } else { seed },
        }
    }

    fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// Uniform-ish index in `0..n`. Caller guarantees `n > 0`.
    fn index(&mut self, n: usize) -> usize {
        (self.next_u64() % n as u64) as usize
    }
}

/// A Markov chain of a given `order` (how many previous words form a state).
pub struct MarkovChain {
    order: usize,
    /// state (the last `order` words) -> every word seen to follow it.
    transitions: HashMap<Vec<String>, Vec<String>>,
    /// the opening states of training lines, used to begin a sentence.
    starts: Vec<Vec<String>>,
}

impl MarkovChain {
    pub fn new(order: usize) -> Self {
        assert!(order >= 1, "order must be >= 1");
        MarkovChain {
            order,
            transitions: HashMap::new(),
            starts: Vec::new(),
        }
    }

    /// Learn transitions from one line of text.
    pub fn train(&mut self, line: &str) {
        let tokens: Vec<String> = line.split_whitespace().map(str::to_string).collect();
        if tokens.len() <= self.order {
            return; // too short to form a state plus a following word
        }
        self.starts.push(tokens[..self.order].to_vec());
        for window in tokens.windows(self.order + 1) {
            let state = window[..self.order].to_vec();
            let next = window[self.order].clone();
            self.transitions.entry(state).or_default().push(next);
        }
    }

    /// Generate a sentence, optionally forcing the first word to `seed`
    /// (matching the Python `/speak <seed>` behaviour). Returns `None` when
    /// there is no usable starting state.
    pub fn generate(&self, rng: &mut Rng, seed: Option<&str>, max_words: usize) -> Option<String> {
        let start: Vec<String> = match seed {
            Some(word) => {
                // Any state beginning with the seed word will do (not just
                // line-openers), so the seed can appear mid-corpus.
                let matching: Vec<&Vec<String>> = self
                    .transitions
                    .keys()
                    .filter(|s| s[0].eq_ignore_ascii_case(word))
                    .collect();
                if !matching.is_empty() {
                    matching[rng.index(matching.len())].clone()
                } else if !self.starts.is_empty() {
                    self.starts[rng.index(self.starts.len())].clone() // fall back
                } else {
                    return None;
                }
            }
            None => {
                if self.starts.is_empty() {
                    return None;
                }
                self.starts[rng.index(self.starts.len())].clone()
            }
        };

        let mut output = start;
        while output.len() < max_words {
            let state = &output[output.len() - self.order..];
            match self.transitions.get(state) {
                Some(nexts) if !nexts.is_empty() => output.push(nexts[rng.index(nexts.len())].clone()),
                _ => break,
            }
        }
        Some(output.join(" "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trained() -> MarkovChain {
        let mut m = MarkovChain::new(1);
        m.train("the cat sat on the mat");
        m.train("the dog sat on the rug");
        m
    }

    #[test]
    fn empty_chain_generates_nothing() {
        let m = MarkovChain::new(1);
        let mut rng = Rng::new(1);
        assert!(m.generate(&mut rng, None, 10).is_none());
    }

    #[test]
    fn generation_is_deterministic_for_a_given_seed_value() {
        let m = trained();
        let a = {
            let mut r = Rng::new(42);
            m.generate(&mut r, None, 20)
        };
        let b = {
            let mut r = Rng::new(42);
            m.generate(&mut r, None, 20)
        };
        assert_eq!(a, b);
    }

    #[test]
    fn output_starts_with_seed_word_when_present() {
        let m = trained();
        let mut rng = Rng::new(7);
        let out = m.generate(&mut rng, Some("dog"), 20).unwrap();
        assert!(out.starts_with("dog"), "got: {out}");
    }

    #[test]
    fn short_lines_are_ignored() {
        let mut m = MarkovChain::new(2);
        m.train("hi there"); // only 2 tokens with order 2 -> no state + next
        let mut rng = Rng::new(1);
        assert!(m.generate(&mut rng, None, 10).is_none());
    }

    #[test]
    fn respects_max_words() {
        let m = trained();
        let mut rng = Rng::new(99);
        let out = m.generate(&mut rng, None, 3).unwrap();
        assert!(out.split_whitespace().count() <= 3);
    }
}
