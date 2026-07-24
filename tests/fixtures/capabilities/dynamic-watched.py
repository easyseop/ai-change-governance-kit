import base64
import importlib
import os


def unknown_getattr(cmd, attr_name):
    return getattr(os, attr_name)(cmd)


def unknown_setattr(attr_name, value):
    setattr(os, attr_name, value)


def unknown_import(module_name):
    return __import__(module_name)


def importlib_unknown(module_name):
    return importlib.import_module(module_name)


def base64_import(encoded):
    return __import__(base64.b64decode(encoded))


def namespace_call(name, payload):
    return globals()[name](payload)


def harmless_object_access(obj, field):
    return getattr(obj, field)
