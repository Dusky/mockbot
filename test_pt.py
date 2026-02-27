import asyncio
from prompt_toolkit.patch_stdout import patch_stdout

async def main():
    with patch_stdout():
        print("\x1b[32mThis should be green\x1b[0m")
        await asyncio.sleep(1)

asyncio.run(main())
