def gov(**kwargs):
    def decorator(func):
        return func
    return decorator


@gov(level="protected", reason="PII report export", owner="security-reviewer", sink=True)
def download_report():
    return "report"


@gov(level="protected", reason="Direct edit only", owner="security-reviewer")
def direct_only():
    return "direct"


def registry_sink():
    return "registered"


def helper():
    return "helper"
