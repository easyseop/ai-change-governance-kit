def keep():
    return "same"


def remove_me(value):
    return value + 1


def body_only(value):
    total = value + 1
    return total


def signature_change(value):
    return value


def outer():
    def inner():
        return "old"
    return inner()


class Account:
    @property
    def balance(self):
        return self._balance

    @balance.setter
    def balance(self, value):
        self._balance = value


def convert(value):
    return value
