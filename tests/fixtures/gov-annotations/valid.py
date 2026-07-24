def plain():
    return "free"


@gov(level="watched", reason="async audit boundary", owner="ops")
async def audit_async():
    return True


@gov(level="frozen", reason="ledger writer")
class LedgerWriter:
    @gov(level="watched", reason="attempted downgrade")
    def commit(self):
        return "commit"

    def inherited(self):
        return "inherited"


class Account:
    @property
    def balance(self):
        return 0

    @balance.setter
    @gov(level="protected", reason="balance mutation")
    def balance(self, value):
        self._balance = value


@overload
@gov(level="watched", reason="overload contract")
def parse(value: str):
    return value


@overload
def parse(value: int):
    return value


if True:
    @gov(level="protected", reason="conditional definition")
    def conditional():
        return True


def outer():
    @gov(level="protected", reason="nested sensitive helper")
    def inner():
        return True

    return inner()


@gov(level="watched", reason="first duplicate")
@gov(level="frozen", reason="second duplicate")
def duplicate_gov():
    return True


@gov(level="frozen", level="watched", reason="duplicate field")
def duplicate_field_gov():
    return True


@gov(level="critical", reason="bad level")
def invalid_level_case():
    return True


LEVEL = "frozen"


@gov(level=LEVEL, reason="dynamic level")
def unresolved_case():
    return True


@gov(level="protected")
def missing_reason_case():
    return True


@gov(level="protected", reason="extra field", ticket="T-1")
def unknown_field_case():
    return True


@gov("protected", reason="positional")
def positional_case():
    return True
