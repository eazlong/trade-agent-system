import functools

def singleton(obj):
    objs = {}

    @functools.wraps(obj)
    def swapper(*args, **kwargs):
        if obj not in objs:
            objs[obj] = obj(*args, **kwargs)
        return objs[obj]

    return swapper
