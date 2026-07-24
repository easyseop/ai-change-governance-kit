import os


def run(cmd, attr_name):
    return getattr(os, attr_name)(cmd)


def run_static(cmd):
    return os.system(cmd)
