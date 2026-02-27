import re

with open('bot/commands.py', 'r') as f:
    content = f.read()

# Replace import sqlite3
content = content.replace("import sqlite3", "import aiosqlite")

# Simple pattern replacements:
# conn = sqlite3.connect(...) \n c = conn.cursor() -> async with aiosqlite.connect(...) as conn:\n    c = await conn.cursor()

# We need to handle indentation properly.
import ast
# To make it easier, let's just make a new file that we wrote manually or via multi_replace.
