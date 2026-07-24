from app.utils import check_permission


def download_report(user):
    check_permission(user)
    return "report"
