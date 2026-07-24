# -*- coding: latin-1 -*-

def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="frozen", reason="legacy café")
def legacy_secret():
    return "café"
