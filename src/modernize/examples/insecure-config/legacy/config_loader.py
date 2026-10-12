"""Configuration loader from an old ops toolkit."""


def parse_value(raw):
    """Turn a config string such as "[1, 2]" or "{'debug': True}" into a value."""
    try:
        return eval(raw)
    except:
        return raw


def load_settings(lines, defaults={}):
    settings = defaults
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, raw = line.partition("=")
        settings[key.strip()] = parse_value(raw.strip())
    return settings


def remember(tag, seen=[]):
    seen.append(tag)
    return seen


def describe(settings):
    return "%d settings: %s" % (len(settings), sorted(settings))
