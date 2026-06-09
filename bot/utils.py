import math
from collections import OrderedDict


class LRUCache:
    """Memory-efficient LRU cache with a hard size cap."""

    def __init__(self, maxsize=1000):
        self.maxsize = maxsize
        self.cache = OrderedDict()

    def __contains__(self, key):
        return key in self.cache

    def __getitem__(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key in self.cache:
            self.cache[key] = value
            self.cache.move_to_end(key)
        else:
            self.cache[key] = value
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def convert_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"
