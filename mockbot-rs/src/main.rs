//! Mockbot (Rust core) — Phase 1 scaffold.
//!
//! For now this just exercises the Markov engine so we can see it working.
//! Later phases add the TUI, chat (twitch-irc), the event bus, EventSub, and the
//! bridge to the Python TTS service.

mod markov;

use markov::{MarkovChain, Rng};

fn main() {
    let corpus = [
        "kreygasm the stream is live and the vibes are good",
        "the bot says hello to the chat every single day",
        "good bots generate good chaos in the chat",
        "the chat loves a good markov moment every time",
    ];

    let mut chain = MarkovChain::new(2);
    for line in corpus {
        chain.train(line);
    }

    let mut rng = Rng::new(0x00C0_FFEE);
    println!("mockbot-rs :: markov demo\n");
    for _ in 0..5 {
        if let Some(msg) = chain.generate(&mut rng, None, 20) {
            println!("  {msg}");
        }
    }
    if let Some(msg) = chain.generate(&mut rng, Some("good"), 20) {
        println!("\n  (seeded 'good') {msg}");
    }
}
