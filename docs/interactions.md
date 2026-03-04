# Twitch Interactions

The bot has built-in support for native Twitch API polls and timed chat messages.

## Creating Twitch Polls

You can launch native Twitch Polls directly from the chat UI without opening the Creator Dashboard.

*   **Syntax**: `!poll <duration_in_minutes> <Question> | <Option 1> | <Option 2> | [Option 3...]`
*   **Example**: `!poll 5 What game should I play? | Mario | Zelda | Halo`

!!! warning
    **Constraints:** Minimum 2 options, maximum 5 options. Duration is bounded between 15 seconds and 30 minutes (1800s).

## Timed Message Pools

You can create rotating lists of messages (like social media plugs or Discord links) that the bot will broadcast at set intervals.

*   **`!mockbot timer add <pool_name> <interval_minutes>`**
    *   Create a new timer pool that triggers every X minutes.
*   **`!mockbot timer msg <pool_name> <Your message text here>`**
    *   Add a message string to the specified pool.
*   **`!mockbot timer list`**
    *   View all active timer pools and their message counts.
*   **`!mockbot timer del <pool_name>`**
    *   Delete a timer pool entirely.
