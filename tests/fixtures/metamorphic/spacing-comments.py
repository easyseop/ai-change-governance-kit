import subprocess


# Formatting-only noise around the same capability signal.
def run_command(command):

    return subprocess.run(command, check=False)
