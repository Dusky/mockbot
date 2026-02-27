import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
        
    content = content.replace("import sqlite3", "import aiosqlite\nimport sqlite3")
    
    # Replace conn = sqlite3.connect(...) with conn = await aiosqlite.connect(...)
    # BUT only inside async functions. bot/commands.py is all inside `async def mockbot_command`
    content = content.replace("sqlite3.connect", "await aiosqlite.connect")
    
    # c = conn.cursor() -> c = await conn.cursor()
    content = re.sub(r'(\w+)\s*=\s*([a-zA-Z0-9_]+)\.cursor\(\)', r'\1 = await \2.cursor()', content)
    
    # c.execute(...) -> await c.execute(...)
    content = re.sub(r'([a-zA-Z0-9_]+)\.execute\(', r'await \1.execute(', content)
    
    # result = c.fetchone() -> result = await c.fetchone()
    content = re.sub(r'(\w+)\s*=\s*([a-zA-Z0-9_]+)\.fetchone\(\)', r'\1 = await \2.fetchone()', content)
    content = re.sub(r'([a-zA-Z0-9_]+)\.fetchone\(\)\[0\]', r'(await \1.fetchone())[0]', content)
    
    # c.fetchall() -> await c.fetchall()
    content = re.sub(r'(\w+)\s*=\s*([a-zA-Z0-9_]+)\.fetchall\(\)', r'\1 = await \2.fetchall()', content)
    content = re.sub(r'([a-zA-Z0-9_]+)\.fetchall\(\)', r'await \1.fetchall()', content)
    
    # conn.commit() -> await conn.commit()
    content = re.sub(r'([a-zA-Z0-9_]+)\.commit\(\)', r'await \1.commit()', content)
    
    # conn.close() -> await conn.close()
    content = re.sub(r'([a-zA-Z0-9_]+)\.close\(\)', r'await \1.close()', content)
    
    # with sqlite3.connect(...) as conn: -> async with aiosqlite.connect(...) as conn:
    # Actually, we replaced sqlite3.connect with await aiosqlite.connect, so "with await aiosqlite.connect" is invalid.
    content = content.replace("with await aiosqlite.connect", "async with aiosqlite.connect")

    with open(filepath, 'w') as f:
        f.write(content)

process_file('bot/commands.py')
