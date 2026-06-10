# Dynamic Channel Variables

Mockbot supports saving numeric variables to a database for your channel. This allows the creation of Death Counters, Win Streaks, and similar trackers.

These variables can be changed using Custom Commands, and they will automatically display on your TTS Web Overlay on stream.

## Building A Death Counter

Let's say you want to let your chat keep track of every time you die in Dark Souls.

**1. Create the command:**
Type this into your Twitch chat:
`!addc !deathadd *BANG* You died! Death Count: <{var:deaths}> {var_add:deaths:1}`

**2. How it works:**
*   `<{var:deaths}>` tells the bot to look into the database, find the number for "deaths", and print it in chat.
*   `{var_add:deaths:1}` is an invisible tag that tells the database to *increase* the death count by 1 for next time.

**3. Try it out:**
If your deaths were at `3`, typing `!deathadd` will print `*BANG* You died! Death Count: 4` in chat. Subsequent executions will continue to increment the value.

## Fixing Mistakes
You can manually set the variable to a specific number using the `var_set` tag.

*   `!addc !fixdeath Setting deaths back to 0! {var_set:deaths:0}`

## Showing It On Stream

Your channel's private TTS source (`http://localhost:5001/tts/<token>`, copied from the web dashboard) will automatically detect your channel's variables and display them as badges beneath the audio visualizer block.
