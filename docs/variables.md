# Dynamic Channel Variables & Web Overlay

Mockbot supports persistent, per-channel numeric variables (like Death Counters or Win Streaks) that are automatically displayed on your stream's TTS Web Overlay.

## Variable Macros

You can read or modify a variable directly inside any Custom Command's response text.

*   **`<{var:name}>`**: Displays the current value of the variable.
*   **`{var_add:name:value}`**: Increments the variable. Use negative numbers to decrement. This tag is invisible in chat.
*   **`{var_set:name:value}`**: Hardcodes the variable to a specific number. This tag is invisible in chat.

!!! example "A Death Counter"
    `!addc !deathadd *BANG* You died! Death Count: <{var:deaths}> {var_add:deaths:1}`
    
    If your deaths were at `3`, typing `!deathadd` will print `*BANG* You died! Death Count: 4` in chat.

## Web API & Stream UI

Whenever a variable is set, it becomes instantly available via the bot's REST API at `/api/variables/<channel>`.

**Displaying on Stream:**

You do not need to do any extra work to show these on stream. The standard TTS Web Overlay (`http://localhost:5050/overlay/<channel>`) will automatically detect your channel's variables and display them as badges beneath the audio visualizer block!
