def check_permission(user):
    return normalize_user(user)


def normalize_user(user):
    return user is not None
