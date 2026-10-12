"""Helpers from the 2012 reporting service."""
import pickle
import string
import time


class Timer(object):
    def __init__(self):
        self.started = time.clock()

    def elapsed(self):
        return time.clock() - self.started


def slugify(title):
    allowed = string.letters + string.digits + "-"
    title = title.replace(" ", "-")
    return "".join(ch for ch in title if ch in allowed).lower()


def progress(done, total):
    ratio = 100.0 * done / total
    return "%d%% complete" % ratio


def load_cache(blob):
    return pickle.loads(blob)
