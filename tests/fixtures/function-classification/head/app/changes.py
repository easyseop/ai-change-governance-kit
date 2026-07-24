def keep():
    return "same"


def added():
    return "new"


def body_only(value):
    total = value + 2
    return total


def signature_change(value, scale=1):
    return value * scale


def outer():
    def inner():
        return "new"
    return inner()


class Account:
    @property
    def balance(self):
        return self._balance

    @balance.setter
    def balance(self, value):
        self._balance = max(value, 0)


def convert(value):
    return value
