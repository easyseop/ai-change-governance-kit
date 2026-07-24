def requires_auth(role):
    def decorate(function):
        return function
    return decorate


@requires_auth("admin")
def secure_view():
    return "ok"


def outer():
    def inner():
        return 2
    return inner()


VALUE = 2


class Service:
    def method(self):
        return "new"


def remove_me():
    return 1
