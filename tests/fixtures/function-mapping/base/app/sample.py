def requires_auth(role):
    def decorate(function):
        return function
    return decorate


@requires_auth("user")
def secure_view():
    return "ok"


def outer():
    def inner():
        return 1
    return inner()


VALUE = 1


class Service:
    def method(self):
        return "old"


def remove_me():
    old = 1
    return old
