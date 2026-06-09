import json
import re
import logging

from bot.config import config


class CustomCommandHandler:
    """Handles Funtoon-style custom commands: Tracery grammar, variable macros, moderation macros."""

    def __init__(self, db, logger, my_logger, bot_ref):
        self.db = db
        self.logger = logger
        self.my_logger = my_logger
        self._bot = bot_ref

    async def handle(self, message, channel_name: str) -> bool:
        """Check if the message matches a custom command and process it. Returns True if handled."""
        command_parts = message.content.split(maxsplit=1)
        if not command_parts:
            return False

        cmd_name = command_parts[0].lower()
        cmd_input = command_parts[1] if len(command_parts) > 1 else ""

        try:
            async with self.db.connect_async() as conn:
                c = await conn.cursor()
                await c.execute(
                    "SELECT response_template FROM custom_commands "
                    "WHERE (channel_name = ? OR channel_name = 'global') AND command_name = ? "
                    "ORDER BY channel_name DESC LIMIT 1",
                    (channel_name, cmd_name),
                )
                cmd_row = await c.fetchone()
                if not cmd_row:
                    return False

                response_template = cmd_row[0]

                # Fetch grammar rules for this channel + global
                await c.execute(
                    "SELECT rule_name, options_json FROM custom_grammar "
                    "WHERE channel_name = ? OR channel_name = 'global'",
                    (channel_name,),
                )
                grammar_rows = await c.fetchall()
                rules = {}
                for rule_name, options_json in grammar_rows:
                    try:
                        rules[rule_name] = json.loads(options_json)
                    except Exception:
                        pass

                # Built-in variables
                rules["sender"] = [message.author.name]
                rules["streamer"] = [channel_name]
                rules["input"] = [cmd_input]

                # Generate response via Tracery
                import tracery
                from tracery.modifiers import base_english
                grammar = tracery.Grammar(rules)
                grammar.add_modifiers(base_english)

                formatted = response_template.replace("<{sender}>", "#sender#")
                formatted = formatted.replace("<{streamer}>", "#streamer#")
                formatted = formatted.replace("<{input}>", "#input#")
                formatted = formatted.replace("\\<", "<").replace("<<", "<")
                generated = grammar.flatten(formatted)

                # Variable macros — {var_add:name:value}
                for var_name, change_val in re.findall(r'\{var_add:(.+?):(-?\d+)\}', generated):
                    try:
                        await c.execute(
                            "INSERT INTO channel_variables (channel_name, var_name, var_value) VALUES (?, ?, ?) "
                            "ON CONFLICT(channel_name, var_name) DO UPDATE SET var_value = var_value + ?",
                            (channel_name, var_name, int(change_val), int(change_val)),
                        )
                        await conn.commit()
                    except Exception as e:
                        self.logger.error(f"Error processing var_add for {var_name}: {e}")

                # Variable macros — {var_set:name:value}
                for var_name, new_val in re.findall(r'\{var_set:(.+?):(-?\d+)\}', generated):
                    try:
                        await c.execute(
                            "INSERT INTO channel_variables (channel_name, var_name, var_value) VALUES (?, ?, ?) "
                            "ON CONFLICT(channel_name, var_name) DO UPDATE SET var_value = ?",
                            (channel_name, var_name, int(new_val), int(new_val)),
                        )
                        await conn.commit()
                    except Exception as e:
                        self.logger.error(f"Error processing var_set for {var_name}: {e}")

                # Strip action tags from output
                generated = re.sub(r'\{var_add:.+?:-?\d+\}', '', generated)
                generated = re.sub(r'\{var_set:.+?:-?\d+\}', '', generated)

                # Read-variable substitution — <{var:X}>
                for var_name in set(re.findall(r'<\{var:(.+?)\}>', generated)):
                    await c.execute(
                        "SELECT var_value FROM channel_variables WHERE channel_name = ? AND var_name = ?",
                        (channel_name, var_name),
                    )
                    row = await c.fetchone()
                    val = row[0] if row else 0
                    generated = generated.replace(f'{{var:{var_name}}}', str(val))
                    generated = generated.replace(f'<{{var:{var_name}}}>', str(val))

                # Moderation macro — {timeout:user:seconds}
                timeout_match = re.search(r'\{timeout:(.+?):(\d+)\}', generated)
                if timeout_match:
                    target_user = timeout_match.group(1).strip()
                    duration = int(timeout_match.group(2))
                    generated = re.sub(r'\{timeout:(.+?):(\d+)\}', '', generated).strip()

                    bot_owner = config.owner.lower()
                    is_mod = message.author.is_mod
                    is_broadcaster = message.author.is_broadcaster
                    is_owner = message.author.name.lower() == bot_owner
                    is_self = target_user.lower() == message.author.name.lower()

                    if is_mod or is_broadcaster or is_owner or is_self:
                        try:
                            channel_users = await self._bot.fetch_users(names=[channel_name])
                            target_users = await self._bot.fetch_users(names=[target_user])
                            if channel_users and target_users and hasattr(self._bot, 'bot_user_id'):
                                tmi_token = config.get("auth", "tmi_token")
                                if tmi_token.startswith("oauth:"):
                                    tmi_token = tmi_token[6:]
                                await channel_users[0].timeout_user(
                                    token=tmi_token,
                                    moderator_id=self._bot.bot_user_id,
                                    user_id=target_users[0].id,
                                    duration=duration,
                                    reason="MockBot Custom Command",
                                )
                                self.logger.info(f"Timed out {target_user} for {duration}s via custom command")
                        except Exception as e:
                            self.logger.error(f"Failed to execute moderation action: {e}")
                    else:
                        self.logger.warning(
                            f"{message.author.name} attempted moderation custom command without permissions."
                        )

                # Send response
                channel_obj = self._bot.get_channel(channel_name)
                if channel_obj and generated:
                    await channel_obj.send(generated)
                    self.my_logger.log_message(channel_name, self._bot.nick, generated, is_bot_message=True)

                return True

        except Exception as e:
            self.logger.error(f"Error processing custom command {cmd_name} in {channel_name}: {e}")
            return False
