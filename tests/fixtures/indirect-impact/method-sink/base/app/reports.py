from app.utils import check_permission


class ReportService:
    def export(self, user):
        check_permission(user)
        return "report"
