# Core Features & Settings

Mockbot operates primarily through Twitch Chat commands using the prefix `!mockbot`. Channel operators and trusted users can configure how the bot behaves natively from the chat.

## Standard Commands

| Command | Description |
|---|---|
| `!mockbot speak` | Forces the bot to instantly generate a Markov-chain response based on the channel's learned vocabulary. If TTS is enabled, it reads it out loud. |
| `!mockbot start` / `!mockbot stop` | Enables or disables the bot's ability to speak automatically in the channel based on message and time thresholds. |
| `!mockbot lines <number>` | Sets the required number of chat messages from users before the bot will automatically generate and send a response. |
| `!mockbot time <seconds>` | Sets the mandatory cooldown period (in seconds) between the bot's automatic chat responses. |
| `!mockbot trust <username>` | Adds a specific user to the channel's "Trusted Users" list, granting them permission to configure the bot, manage timers, and create custom commands. |
| `!mockbot join <#channel>` / `!mockbot part <#channel>` | *(Global Bot Owner Only)* Instructs the bot to enter or leave a specific Twitch channel. |

## PubSub Integrations

Mockbot can listen to native Twitch events (using your User Auth Token) and respond automatically.

*   **`!mockbot bits <on/off>`**: Enables or disables the bot's reaction to Bits/Cheers.
*   **`!mockbot points <on/off>`**: Enables or disables the bot's reaction to Channel Point redemptions.
