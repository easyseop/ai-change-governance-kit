import subprocess as sp
from subprocess import run as r
from subprocess import *
import subprocess
import pickle
import requests
import ssl
import hmac
import yaml
import urllib.request


def use_alias(cmd):
    return sp.run(cmd)


def use_from_alias(cmd):
    return r(cmd)


def use_dynamic_access(cmd):
    return getattr(subprocess, "run")(cmd)


def use_dynamic_import():
    return __import__("subprocess")


def use_builtins(code, expr):
    exec(code)
    return eval(expr)


def use_internal_import(blob):
    import pickle as pk
    return pk.loads(blob)


def use_deserialization(blob):
    return yaml.load(blob)


def use_network(url):
    return urllib.request.urlopen(url)


def use_crypto(key, msg):
    hmac.new(key, msg)
    return ssl.wrap_socket(None)


def use_importlib(name):
    import importlib
    return importlib.import_module(name)


def use_call_only_getattr(cmd):
    import os
    return getattr(os, "system")(cmd)


def use_call_only_reassigned(cmd):
    import os
    runner = os
    return runner.system(cmd)


def use_call_only_getattr_assigned(cmd):
    import os
    command_runner = getattr(os, "system")
    return command_runner(cmd)


def use_call_only_alias_getattr_assigned(cmd):
    import os as o
    alias_runner = getattr(o, "system")
    return alias_runner(cmd)
