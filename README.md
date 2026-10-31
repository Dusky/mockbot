# mockbot

Twitch Markov chain and Suno Bark TTS bot written in Rust.

## Building

```bash
cp config.example.toml config.toml
cargo build --release
```

## Usage

```bash
# Headless daemon (Twitch IRC + WebUI on :5002 + Bark TTS worker)
./target/release/ansv2 daemon

# Terminal UI (attaches to running daemon or runs standalone)
./target/release/ansv2 tui

# Corpus stats
./target/release/ansv2 stats

# Test generation from CLI
./target/release/ansv2 generate --channel firestarman -n 5
```
