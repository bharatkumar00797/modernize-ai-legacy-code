import pickle

import report_helpers


def test_timer_measures_elapsed_time():
    timer = report_helpers.Timer()
    assert timer.elapsed() >= 0


def test_slugify():
    assert report_helpers.slugify("Hello World 2!") == "hello-world-2"


def test_progress_truncates_like_the_old_service():
    assert report_helpers.progress(1, 3) == "33% complete"
    assert report_helpers.progress(2, 3) == "66% complete"


def test_cache_roundtrip():
    assert report_helpers.load_cache(pickle.dumps({"a": 1})) == {"a": 1}
