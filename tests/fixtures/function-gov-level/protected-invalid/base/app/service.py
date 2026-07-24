def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="protected", reason="auth boundary")
def check_auth(user):
    return user.is_active


@gov(level="watched")
def invalid_annotation():
    return "base"
