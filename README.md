# Mockbot (CLI Edition)

A sophisticated Twitch chat bot with AI-powered text generation (Markov chains) and Text-to-Speech (Bark).

Now fully refactored into a lightweight, efficient CLI application.

## Key Features

- **AI Text Generation**: Learns from chat logs using Markov chains.
- **Text-to-Speech**: High-quality TTS using [Bark](https://github.com/suno-ai/bark) (NVIDIA GPU recommended).
- **CLI Dashboard**: Simple, resource-efficient terminal interface.
- **Multi-Channel**: Supports joining multiple channels with individual settings.

## Quick Start

### 1. Installation

```bash
# Clone and setup
git clone https://github.com/yourusername/mockbot.git
cd mockbot
./launch.sh setup
```

### 2. Configuration

Edit `settings.conf` (created during setup) with your credentials:

```ini
[auth]
tmi_token = oauth:your_token  # Get from twitchapps.com/tmi
client_id = your_client_id    # optional for basic features
nickname = your_bot_name
owner = your_username

[settings]
channels = channel1, channel2
verbose_heartbeat_log = false
```

### 3. Run

```bash
# Start the bot
./launch.sh cli
```

## Commands

Commands are prefixed with `!mockbot` (or `!mb` if configured).

| Command | Usage | Description |
|---------|-------|-------------|
| **Speak** | `!mockbot speak` | Force the bot to generate a message (and speak if TTS on). |
| **TTS** | `!mockbot tts on/off` | Enable/Disable TTS for the channel. |
| **Join** | `!mockbot join <channel>` | Join a new channel (Owner only). |
| **Part** | `!mockbot part <channel>` | Leave a channel (Owner only). |
| **Config** | `!mockbot lines <num>` | Set messages required before auto-reply. |

## Interactive CLI Dashboard
The bot comes completely equipped with a `screen`-like interface inside the terminal.
Start it by running `./launch.sh cli`.
In the CLI dashboard, you can swap between channels using `use #channel`, adjust global settings with `use` to return to global config, and view internal database caches with `brain`. 
You can assign specific voices to specific channels using `set voice <model_name>` while inside a channel context.

## Custom Web Notification Overlay
Mockbot spins up a lightweight `aiohttp` web server attached to port `5050`. 
Add a "Browser Source" to your OBS layout with the URL `http://localhost:5050/overlay/<your_channel>`.
Whenever the bot speaks, it will slide on screen with a stylish Cyber-Noir text widget, type the message out, play the generated Bark Audio, and slide away.

## File Structure

```
.
├── main.py             # Entry point
├── bot/                # Core logic package
│   ├── core.py         # Bot class & Event loop
│   ├── commands.py     # Command definitions
│   ├── tts.py          # Bark TTS integration
│   └── ...
├── launch.sh           # Management script
└── logs/               # Log files
```

## Troubleshooting

- **TTS Slow?** ensure you have an NVIDIA GPU and installed via `./launch.sh setup-tts`.
- **Bot silent?** Check `logs/mockbot.log`. Ensure `lines_between_messages` isn't too high.