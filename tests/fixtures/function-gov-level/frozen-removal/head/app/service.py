def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


def ledger_write():
    return "head"
