import os


def run(cmd):
    command_runner = getattr(os, "system")
    return command_runner(cmd)
