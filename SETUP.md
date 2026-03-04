# Setup Guide

## Prerequisites

- **Python 3.8+**
- **NVIDIA GPU** (Highly recommended for TTS)
- **Twitch Account** for the bot

## Step-by-Step

1.  **Run Start Script**
    ```bash
    ./launch.sh start
    ```
    This automatically creates the virtual environment, installs core dependencies, and spins up the background worker.

2.  **Install TTS Dependencies (Optional)**
    If you want the bot to speak:
    ```bash
    ./launch.sh setup-tts
    ```
    *Note: This downloads ~3GB of models (PyTorch, Transformers).*

3.  **Configure Credentials**
    Open `settings.conf`:
    *   `tmi_token`: Get this from [https://twitchapps.com/tmi/](https://twitchapps.com/tmi/) (exclude 'oauth:' if the generator provides it, or include it, the bot handles both).
    *   `nickname`: The exact username of the bot account.
    *   `owner`: Your twitch username (for admin commands).
    *   *(Note: Channels are managed in-app via the CLI and database!)*

4.  **Launch**
    ```bash
    ./launch.sh cli
    ```

## Maintenance

- **Documentation**: Provide `./launch.sh docs` to open the local wiki!
- **Logs**: View logs with `./launch.sh logs`
- **Clean**: `./launch.sh clean` (Removes temp logs and PIDs)
