def decorator(fn):
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper

def outer(value):
    def inner():
        return value
    return inner()

class Service:
    @decorator
    async def load(self):
        return "ok"

    class Nested:
        def method(self):
            return 1

@decorator
def decorated():
    return Service()
