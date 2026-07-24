def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="protected", reason="auth boundary")
def reset_password():
    return "base"
