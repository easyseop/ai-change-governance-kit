import subprocess as sp


def run_command(command):
    return sp.run(command, check=False)
