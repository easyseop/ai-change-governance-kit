if True:
    def load_value():
        return normalize()
else:
    def load_value():
        return fallback()


def normalize():
    return "normalized"


def fallback():
    return "fallback"
