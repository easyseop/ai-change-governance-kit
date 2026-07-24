from app import utils


def download_report(user, check_name):
    return dispatch(user, check_name)


def dispatch(user, check_name):
    return getattr(utils, check_name)(user)
