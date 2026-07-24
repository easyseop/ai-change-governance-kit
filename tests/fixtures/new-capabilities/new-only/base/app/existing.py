import subprocess


def already_sensitive(cmd):
    return subprocess.run(cmd)
