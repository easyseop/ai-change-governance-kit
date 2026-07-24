def gov(**kwargs):
    def decorator(target):
        return target
    return decorator


@gov(level="protected", reason="auth boundary")
def check_auth(user):
    return user.is_active and user.has_session


@gov(level="watched")
def invalid_annotation():
    return "head"
