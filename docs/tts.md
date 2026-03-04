# Text-to-Speech (TTS)

Mockbot uses the high-quality **Bark AI** model for Text-to-Speech generation. TTS is routed through a web overlay designed as an OBS Browser Source, so it doesn't try to play directly through your Python terminal.

## Core Commands

*   **`!mockbot tts <on/off>`**
    *   Turn Text-to-Speech entirely on or off for your channel. When enabled, all generated Markov messages will be spoken.
*   **`!mockbot voice_preset <preset_name>`**
    *   Change the voice the bot uses. Available Bark presets usually follow the format `v2/en_speaker_1`, `v2/en_speaker_9`, etc.

## The Web Overlay

To hear the bot and display real-time channel variables, you must add the OBS overlay to your streaming software.

*   **URL:** `http://localhost:5050/overlay/<your_channel_name>`
*   **Dimensions:** Default `800x600` is fine, but it is responsive.
*   **Permissions:** You must check "Control Audio via OBS" if you wish to adjust the TTS volume inside your mixer instead of Desktop Audio.
