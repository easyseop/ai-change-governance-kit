import os


def run(cmd, attr_name):
    return getattr(os, attr_name)(cmd)
