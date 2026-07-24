from app.utils import check_permission as allow
from app.conditional import load_value


def download_report(user):
    allow(user)
    render_report(user)
    load_value()
    return "report"


def render_report(user):
    return format_report(user)


def format_report(user):
    return f"report:{user}"


def dynamic_dispatch(obj, name):
    return getattr(obj, name)()


class ReportService:
    def export(self, user):
        render_report(user)
        return user
