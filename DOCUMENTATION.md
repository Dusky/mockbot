# Mockbot Comprehensive Documentation

Welcome to the complete guide for Mockbot! This document covers all of the bot's core features, settings, and its powerful Custom Command & Generative Grammar engine.

---

## 📚 Table of Contents
1. [Core Features & Settings](#1-core-features--settings)
2. [Twitch Interactions (Polls & Timers)](#2-twitch-interactions-polls--timers)
3. [Text-to-Speech (TTS)](#3-text-to-speech-tts)
4. [Custom Commands & Funtoon Grammar](#4-custom-commands--funtoon-grammar)
5. [Advanced: Moderation Actions (Timeouts)](#5-advanced-moderation-actions-timeouts)
6. [Dynamic Channel Variables & Web Overlay](#6-dynamic-channel-variables--web-overlay)

---

## 1. Core Features & Settings

Mockbot operates primarily through Twitch Chat commands using the prefix `!mockbot`. Channel operators and trusted users can configure how the bot behaves natively from the chat.

### Standard Commands
*   **`!mockbot speak`**
    *   **Description**: Forces the bot to instantly generate a Markov-chain response based on the channel's learned vocabulary. If TTS is enabled, the bot will also read the message out loud on the overlay.
*   **`!mockbot start` / `!mockbot stop`**
    *   **Description**: Enables or disables the bot's ability to speak automatically in the channel based on message and time thresholds.
*   **`!mockbot lines <number>`**
    *   **Description**: Sets the required number of chat messages from users before the bot will automatically generate and send a response.
*   **`!mockbot time <seconds>`**
    *   **Description**: Sets the mandatory cooldown period (in seconds) between the bot's automatic chat responses.
*   **`!mockbot trust <username>`**
    *   **Description**: Adds a specific user to the channel's "Trusted Users" list, granting them permission to configure the bot, manage timers, and create custom commands.
*   **`!mockbot join <#channel>` / `!mockbot part <#channel>`**
    *   **Description**: *(Global Bot Owner Only)* Instructs the bot to enter or leave a specific Twitch channel.

### PubSub Integrations
Mockbot can listen to native Twitch events and respond automatically.
*   **`!mockbot bits <on/off>`**
    *   **Description**: Enables or disables the bot's reaction to Bits/Cheers.
*   **`!mockbot points <on/off>`**
    *   **Description**: Enables or disables the bot's reaction to Channel Point redemptions.

---

## 2. Twitch Interactions (Polls & Timers)

The bot has built-in support for native Twitch API polls and timed chat messages.

### Creating Twitch Polls
You can launch native Twitch Polls directly from the chat UI without opening the Creator Dashboard.

*   **Syntax**: `!poll <duration_in_minutes> <Question> | <Option 1> | <Option 2> | [Option 3...]`
*   **Example**: `!poll 5 What game should I play? | Mario | Zelda | Halo`
*   **Constraints**: Minimum 2 options, maximum 5 options. Duration is bounded between 15 seconds and 30 minutes (1800s).

### Timed Message Pools
You can create rotating lists of messages (like social media plugs or Discord links) that the bot will broadcast at set intervals.

*   **`!mockbot timer add <pool_name> <interval_minutes>`**
    *   *Create a new timer pool that triggers every X minutes.*
*   **`!mockbot timer msg <pool_name> <Your message text here>`**
    *   *Add a message to the specified pool.*
*   **`!mockbot timer list`**
    *   *View all active timer pools and their message counts.*
*   **`!mockbot timer del <pool_name>`**
    *   *Delete a timer pool entirely.*

---

## 3. Text-to-Speech (TTS)

Mockbot uses the high-quality **Bark AI** model for Text-to-Speech generation. TTS is routed through a web overlay designed as an OBS Browser Source.

*   **`!mockbot tts <on/off>`**
    *   **Description**: Turn Text-to-Speech entirely on or off for your channel.
*   **`!mockbot voice_preset <preset_name>`**
    *   **Description**: Change the voice the bot uses. Available Bark presets usually follow the format `v2/en_speaker_1`, `v2/en_speaker_9`, etc.
*   **Connecting the Overlay**: Add a Browser Source in OBS pointing to `http://localhost:5050/overlay/<your_channel>`.

---

## 4. Custom Commands & Funtoon Grammar

Mockbot's most powerful feature is its **Custom Command Engine**, inspired heavily by the popular Funtoon Grammar implementation. It is powered by [Tracery](https://tracery.io/), allowing you to build infinitely randomizing chat responses, minigames, and interactive commands.

### Managing Commands
*   **`!addc !<command_name> <response>`**: Create a new custom command.
*   **`!editc !<command_name> <new_response>`**: Modify an existing command.
*   **`!delc !<command_name>`**: Delete a custom command.

### Dynamic Variables (Funtoon Syntax)
Commands become interactive when using "Variables". When a custom command fires, Mockbot replaces these tags dynamically:

*   **`<{sender}>`**: The username of the person who typed the command.
*   **`<{input}>`**: Anything the user typed *after* the command.
*   **`<{streamer}>`**: The name of the channel the command was used in.

*Example:* 
`!addc !slap <{sender}> slaps <{input}>!`
If `firestarman` types `!slap the wall`, the bot outputs: `firestarman slaps the wall!`

### Tracery Grammar (Word Pools)
Instead of static text, you can tell the bot to pull random words or phrases from "Grammar Rules". Rules are surrounded by hashtags `#rule_name#`.

**1. Define your Word Pools using `!grammar`:**
*   `!grammar add weapons a huge trout`
*   `!grammar add weapons a folding chair`
*   `!grammar add weapons the ban hammer`

**2. Use the pool in a custom command:**
*   `!addc !attack <{sender}> strikes <{input}> with #weapons#!`

Now, typing `!attack the boss` might result in `firestarman strikes the boss with a folding chair!` or `...with a huge trout!`. The possibilities are endless.

**Managing Grammar Rules:**
*   `!grammar list <rule>`: Show all options inside a rule.
*   `!grammar clear <rule>`: Delete the rule and all its options.

---

## 5. Advanced: Moderation Actions (Timeouts)

Custom commands can execute **real Twitch moderation actions** (like Timing Out users)! This is perfect for "Russian Roulette" style commands.

### The Timeout Tag
To issue a timeout, include the hidden `{timeout:user:duration}` tag anywhere in your command's response template. **The tag itself is completely invisible in chat.**

*   **Syntax**: `{timeout:target_user:duration_in_seconds}`
*   **Usage with Variables**: `{timeout:<{input}>:60}` (Times out the person the sender targeted for 60 seconds).
*   **Self-Timeout Example**: `{timeout:<{sender}>:10}` (Times out the person who ran the command for 10 seconds).

### Security & Permissions
To prevent abuse, Mockbot will **ONLY** execute a `{timeout...}` tag if the user executing the command meets one of the following criteria:
1. They are a Channel Moderator.
2. They are the Broadcaster.
3. They are the Global Bot Owner.
4. **They are targeting themselves.** (Even non-mods can use commands that timeout *themselves*).

### Example: Russian Roulette
Using Grammar and Timeouts together, we can make a Russian Roulette game.

**1. Setup the outcomes (Grammar):**
*   `!grammar add roulette <{sender}> survives the spin... this time.`
*   `!grammar add roulette *BANG* <{sender}> was shot! {timeout:<{sender}>:60}`

**2. Create the command:**
*   `!addc !spin #roulette#`

If a normal viewer types `!spin`, they have a 50/50 chance of surviving or being timed out for 60 seconds automatically by the bot!

---

## 6. Dynamic Channel Variables & Web Overlay

Mockbot supports persistent, per-channel numeric variables (like Death Counters or Win Streaks) that are automatically displayed on your stream's TTS Web Overlay.

### Variable Macros
You can read or modify a variable directly inside any Custom Command's response text.

*   **`<{var:name}>`**: Displays the current value of the variable.
*   **`{var_add:name:value}`**: Increments the variable. Use negative numbers to decrement. This tag is invisible in chat.
*   **`{var_set:name:value}`**: Hardcodes the variable to a specific number. This tag is invisible in chat.

**Example: A Death Counter**
```text
!addc !deathadd *BANG* You died! Death Count: <{var:deaths}> {var_add:deaths:1}
```
If your deaths were at `3`, typing `!deathadd` will print `*BANG* You died! Death Count: 4` in chat.

### Web API & Stream UI
Whenever a variable is set, it becomes instantly available via the bot's REST API at `/api/variables/<channel>`.

**Displaying on Stream:**
You do not need to do any extra work to show these on stream. The standard TTS Web Overlay (`http://localhost:5050/overlay/<channel>`) will automatically detect your channel's variables and display them as badges beneath the audio visualizer block!
