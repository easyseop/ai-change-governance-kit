import os as o


def run(cmd):
    alias_runner = getattr(o, "system")
    return alias_runner(cmd)
