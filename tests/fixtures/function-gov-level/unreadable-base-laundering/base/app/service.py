def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="watched", reason="shared helper")
def normalize(value):
    return value.strip()
