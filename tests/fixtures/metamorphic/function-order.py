import subprocess


def helper(value):
    return value.strip()


def run_command(command):
    return subprocess.run(command, check=False)
