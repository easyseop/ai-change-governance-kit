def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="frozen", reason="ledger write path")
def ledger_write():
    return "base"
