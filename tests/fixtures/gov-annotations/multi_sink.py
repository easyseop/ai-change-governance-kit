@gov(level="frozen")
@gov(level="protected", reason="PII bulk export", owner="security-reviewer", sink=true)
def download_bulk():
    return "bulk"
