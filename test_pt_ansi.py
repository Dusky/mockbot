import asyncio
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit import print_formatted_text, ANSI
import builtins

_orig_print = builtins.print
def custom_print(*args, **kwargs):
    # If no file is specified or it's stdout
    file = kwargs.get('file', None)
    if file is None:
        text = " ".join(str(arg) for arg in args)
        print_formatted_text(ANSI(text), kwargs.get('end', '\n'))
    else:
        _orig_print(*args, **kwargs)

builtins.print = custom_print

async def main():
    with patch_stdout():
        print("\x1b[32mThis should be green!\x1b[0m")
        await asyncio.sleep(0.5)

asyncio.run(main())
