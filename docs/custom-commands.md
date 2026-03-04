# Custom Commands & Grammar

Mockbot's most powerful feature is its **Custom Command Engine**, inspired heavily by the popular Funtoon Grammar implementation. It is powered by [Tracery](https://tracery.io/), allowing you to build infinitely randomizing chat responses, minigames, and interactive commands.

## Managing Commands

*   **`!addc !<command_name> <response>`**: Create a new custom command.
*   **`!editc !<command_name> <new_response>`**: Modify an existing command.
*   **`!delc !<command_name>`**: Delete a custom command.

## Dynamic Variables (Funtoon Syntax)

Commands become interactive when using "Variables". When a custom command fires, Mockbot replaces these tags dynamically:

*   **`<{sender}>`**: The username of the person who typed the command.
*   **`<{input}>`**: Anything the user typed *after* the command.
*   **`<{streamer}>`**: The name of the channel the command was used in.

!!! tip "Example"
    `!addc !slap <{sender}> slaps <{input}>!`
    
    If `firestarman` types `!slap the wall`, the bot outputs: `firestarman slaps the wall!`

## Tracery Grammar (Word Pools)

Instead of static text, you can tell the bot to pull random words or phrases from "Grammar Rules". Rules are surrounded by hashtags `#rule_name#`.

**1. Define your Word Pools using `!grammar`:**

*   `!grammar add weapons a huge trout`
*   `!grammar add weapons a folding chair`
*   `!grammar add weapons the ban hammer`

**2. Use the pool in a custom command:**

*   `!addc !attack <{sender}> strikes <{input}> with #weapons#!`

Now, typing `!attack the boss` might result in `firestarman strikes the boss with a folding chair!` or `...with a huge trout!`. The possibilities are endless.

!!! note "Managing Grammar"
    *   `!grammar list <rule>`: Show all options inside a rule.
    *   `!grammar clear <rule>`: Delete the rule and all its options.

## Moderation Actions & Timeouts

Custom commands can execute **real Twitch moderation actions** (like Timing Out users)! This is perfect for "Russian Roulette" style commands.

### The Timeout Tag

To issue a timeout, include the hidden `{timeout:user:duration}` tag anywhere in your command's response template. **The tag itself is completely invisible in chat.**

*   **Syntax**: `{timeout:target_user:duration_in_seconds}`
*   **Usage with Inputs**: `{timeout:<{input}>:60}`
*   **Self-Timeout Example**: `{timeout:<{sender}>:10}`

### Security & Permissions

To prevent abuse, Mockbot will **ONLY** execute a `{timeout...}` tag if the user executing the command meets one of the following criteria:

1. They are a Channel Moderator.
2. They are the Broadcaster.
3. They are the Global Bot Owner.
4. **They are targeting themselves.** (Even non-mods can use commands that timeout *themselves*).

!!! example "Russian Roulette Game"
    **1. Setup the outcomes (Grammar):**
    
    *   `!grammar add roulette <{sender}> survives the spin... this time.`
    *   `!grammar add roulette *BANG* <{sender}> was shot! {timeout:<{sender}>:60}`
    
    **2. Create the command:**
    
    *   `!addc !spin #roulette#`
    
    If a normal viewer types `!spin`, they have a 50/50 chance of surviving or being timed out for 60 seconds automatically by the bot!
