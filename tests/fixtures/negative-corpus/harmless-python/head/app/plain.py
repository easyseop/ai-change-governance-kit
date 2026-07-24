"""Plain helpers without sensitive capabilities."""


def normalize(value):
    # Formatting and comments should not create governance warnings.
    return value.strip()


def format_label(value):
    return normalize(value).title()
