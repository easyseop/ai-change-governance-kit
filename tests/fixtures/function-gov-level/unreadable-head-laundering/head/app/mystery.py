# -*- coding: latin-1 -*-

def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="frozen", reason="hidden café")
def hidden():
    return "café"
