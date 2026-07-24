from app.utils import check_permission


def gov(**kwargs):
    def decorator(func):
        return func
    return decorator


@gov(level="protected", reason="PII export", owner="security-reviewer", sink=True)
def download_report(user):
    check_permission(user)
    return "report"


@gov(level="protected", reason="Direct edit only", owner="security-reviewer")
def direct_only(user):
    check_permission(user)
    return "direct"
