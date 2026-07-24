from app import overloads as overloads_module


def foo():
    return "module"


def sink():
    return "module sink"


class C:
    def foo(self):
        return "class"

    def sink(self):
        return "class sink"

    def caller(self):
        return sink()


def bar():
    return foo()


def qualified_bar():
    return overloads_module.sink()
