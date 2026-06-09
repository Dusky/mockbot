"""Event/command handler modules for the Bot.

Each module holds the body of a TwitchIO event or command as a function taking
the Bot instance as its first argument. The Bot class keeps thin delegating
methods so TwitchIO's name-based dispatch (event_ready, event_message, ...) and
the external interface are preserved — mirrors the bot/tasks/ package pattern.
"""
