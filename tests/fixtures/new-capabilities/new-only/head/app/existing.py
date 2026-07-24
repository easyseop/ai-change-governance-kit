import subprocess


def already_sensitive(cmd):
    return subprocess.run(cmd)


def extra_use(cmd):
    return subprocess.Popen(cmd)
