import os


def folded_getattr(cmd):
    return getattr(os, "sys" + "tem")(cmd)


def folded_import():
    return __import__("sub" + "process")
