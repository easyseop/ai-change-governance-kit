def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="frozen", reason="deleted frozen function")
def cannot_delete():
    return "base"
