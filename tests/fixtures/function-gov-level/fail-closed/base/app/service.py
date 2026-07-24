def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="protected", reason="base sensitive function")
def sensitive():
    return "base"
