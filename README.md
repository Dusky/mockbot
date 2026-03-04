# Mockbot (CLI Edition)

A sophisticated Twitch chat bot with AI-powered text generation (Markov chains) and Text-to-Speech (Bark).

Now fully refactored into a lightweight, efficient CLI application.

## Key Features

- **AI Text Generation**: Learns from chat logs using Markov chains to create realistic bot personas.
- **Custom Generative Grammar**: A powerful command engine powered by [Tracery](https://tracery.io/). Channel operators can define commands (e.g., `!slap`) that pull from robust randomization rules.
- **Interactive Twitch Integrations**: Natively hooks into Twitch **Channel Points** and **Bits (Cheers)**. Channel Point redemptions can even trigger Tracery commands dynamically!
- **Twitch Chat Polls**: Operators and Bot Owners can seamlessly spawn Polls directly from Twitch Chat or the Terminal CLI
- **Text-to-Speech**: High-quality TTS using [Bark](https://github.com/suno-ai/bark)
- **Interactive Dashboard**: A `screen`-styled Terminal CLI used to live-manage, configure, and monitor the bot across all channels.

## Quick Start

### 1. Installation

```bash
# Clone and setup
git clone https://github.com/yourusername/mockbot.git
cd mockbot
./launch.sh start
```

### 2. Configuration

Edit `settings.conf` (created during setup) with your credentials:

```ini
[auth]
tmi_token = oauth:your_token  # Required for Chat & PubSub
client_id = your_client_id    # Required for API calls (e.g. Polls)
nickname = your_bot_name
owner = your_username

[settings]
# Channels are now managed via SQLite. Use the CLI `join <#channel>` 
# or Twitch chat `!mockbot join` command to configure them!
```

### 3. Run

```bash
# Start the bot monitor CLI
./launch.sh cli
```

## Commands (Twitch Chat)

> [!NOTE]
> For a full, comprehensive breakdown of all features, custom commands, timers, and Tracery grammar syntax, please check out the **Mockbot Wiki**!
> You can launch the Wiki locally at any time by running `./launch.sh docs`.

Commands are prefixed with `!mockbot` (or `!mb` if configured), except for dedicated actions.

| Command | Usage | Description |
|---------|-------|-------------|
| **Speak** | `!mockbot speak` | Force the bot to generate a message (and speak if TTS on). |
| **Settings** | `!mockbot <setting> <on/off>` | Manage `tts`, `voice`, `bits`, `points`, `lines`, `time`. |
| **Custom Commands** | `!addc`, `!editc`, `!delc` | Manage Tracery rules (e.g., `!addc !test <{sender}> said <{input}>`) |
| **Grammar Rules** | `!grammar <add/list/clear> <rule>` | Register word-pools for use in Custom Commands. |
| **Polls** | `!poll <mins> <Q> \| <A1> \| <A2>` | Start a native Twitch chat UI poll. |

## Interactive CLI Dashboard

The bot comes completely equipped with an interactive terminal interface! Start it by running `./launch.sh cli`.

In the CLI dashboard, you can swap between channels using `use #channel`, adjust global settings with `use` to return to global config, and view internal database caches with `status`.
You can live-manage features natively from the server terminal:
- Add commands: `addc !test Hello <{sender}>`
- Manage Polls: `poll 2 What is cool? | Everything | Nothing`
- Enable/Disable Triggers: `set bits on` / `set points on`

## Custom Web Notification Overlay

Mockbot spins up a lightweight `aiohttp` web server attached to port `5050`. 
Add a "Browser Source" to your OBS layout with the URL `http://localhost:5050/overlay/<your_channel>`.
Whenever the bot speaks, it will slide on screen with a stylish Cyber-Noir text widget, type the message out, play the generated Bark Audio, and slide away.

## File Structure

```text
.
├── main.py             # Entry point
├── bot/                # Core logic package
│   ├── core.py         # Bot class, Subscriptions, & Event loops
│   ├── commands.py     # Command parsing & logic
│   ├── tts.py          # Bark TTS generation queue
│   ├── tui.py          # Terminal UI
│   └── ...
├── docs/               # MkDocs Wiki Documentation
├── launch.sh           # Setup & Management script
├── messages.db         # Persistent context and commands DB
└── logs/               # Traceback log files
```