# Text-to-Speech (TTS)

Mockbot uses the **Bark AI** model to read out generated sentences.

Because routing TTS audio directly through a headless Python terminal is impractical, Mockbot uses a "Web Overlay" instead. This functions similarly to Streamlabs or StreamElements alerts.

## Core Commands

*   `!mockbot tts on` / `!mockbot tts off`
    *   Turn Text-to-Speech entirely on or off for your channel. When enabled, all generated Markov messages will be spoken.
*   `!mockbot set voice <preset_name>`
    *   Change the voice the bot uses. Available Bark presets look like this: `v2/en_speaker_1`, `v2/en_speaker_9`, etc.

## Putting the Bot on Stream

To hear the bot and display real-time channel variables, you just need to add the OBS overlay to your streaming software.

1. Log into the web dashboard at `http://localhost:5001` and copy your channel's **private TTS source URL** (it looks like `http://localhost:5001/tts/<token>`). You can rotate the token there if it ever leaks.
2. Open **OBS Studio**.
3. Add a new **Browser Source** and set the **URL** to your private TTS source URL from step 1.
4. Set the **Width/Height** to `800x600`.
5. Check **"Control Audio via OBS"** if you want to be able to adjust the bot's volume from your audio mixer.
