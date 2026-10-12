# -*- coding: utf-8 -*-
"""Small text helpers from a Python 2 code base."""


def banner(title, width=20):
    line = "=" * width
    print line
    print title.center(width)
    print line
    return "%s\n%s\n%s" % (line, title.center(width), line)


def number_lines(text):
    lines = text.splitlines()
    out = []
    for i in xrange(len(lines)):
        out.append("%d: %s" % (i + 1, lines[i]))
    return out


def to_text(value):
    if isinstance(value, unicode):
        return value
    return unicode(value)


def word_counts(text):
    counts = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    parts = []
    for word, n in sorted(counts.iteritems()):
        parts.append("%s=%d" % (word, n))
    return ", ".join(parts)


class Greeter(object):
    def __init__(self, name):
        self.name = name

    def greet(self):
        return "Hello, %s!" % self.name
