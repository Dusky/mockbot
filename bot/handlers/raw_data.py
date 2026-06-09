"""Raw IRC data handling — surface Twitch NOTICE messages as actionable warnings."""
from bot.colors import RED, RESET


async def handle_raw_data(bot, data: str):
    """Intercept raw IRC data to catch Twitch dropping messages or sending notices."""
    if "NOTICE" not in data:
        return

    bot.logger.info(f"{RED}Twitch IRC NOTICE: {data.strip()}{RESET}")
    try:
        # Format: @msg-id=... :tmi.twitch.tv NOTICE #channel :Message
        parts = data.split(" :", 1)
        if len(parts) > 1:
            notice_msg = parts[1].strip()
            # Extract channel if present
            channel = "global"
            if " NOTICE #" in data:
                channel = data.split(" NOTICE #")[1].split(" :")[0].strip()

            # Format user-friendly actionable notices
            display_msg = f"[bold red]NOTICE: {notice_msg}[/bold red]"

            if "follower-only" in notice_msg.lower():
                display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} is in Follower-Only mode!\n[cyan]Action Required:[/cyan] Log into the bot's Twitch account and go to https://twitch.tv/{channel} to follow them, or type '/mod {bot.nick}' from the broadcaster account."
            elif "verified phone number" in notice_msg.lower():
                display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} requires a verified phone number!\n[cyan]Action Required:[/cyan] Go to https://www.twitch.tv/settings/security to verify the bot's phone number, or type '/mod {bot.nick}' from the broadcaster account."
            elif "verified email" in notice_msg.lower():
                display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} requires a verified email address!\n[cyan]Action Required:[/cyan] Go to https://www.twitch.tv/settings/security to verify the bot's email, or type '/mod {bot.nick}' from the broadcaster account."
            elif "subscriber" in notice_msg.lower():
                display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} is in Subscriber-Only mode!\n[cyan]Action Required:[/cyan] You must subscribe to the channel, or type '/mod {bot.nick}' from the broadcaster account."
            elif "banned" in notice_msg.lower():
                display_msg = f"[bold red]🚫 BANNED[/bold red]: The bot is banned from talking in #{channel}."

            bot.my_logger.log_message(channel, "TwitchSystem", display_msg, is_bot_message=True)
    except Exception:
        pass
