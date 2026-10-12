import config_loader


def test_parses_literals():
    assert config_loader.parse_value("[1, 2]") == [1, 2]
    assert config_loader.parse_value("{'debug': True}") == {"debug": True}
    assert config_loader.parse_value("3.5") == 3.5


def test_plain_strings_pass_through():
    assert config_loader.parse_value("hello world") == "hello world"


def test_config_values_are_never_executed():
    payload = "__import__('os').getcwd()"
    assert config_loader.parse_value(payload) == payload


def test_defaults_are_not_shared_between_calls():
    first = config_loader.load_settings(["a = 1"])
    second = config_loader.load_settings(["b = 2"])
    assert first == {"a": 1}
    assert second == {"b": 2}


def test_remember_starts_fresh_each_call():
    assert config_loader.remember("x") == ["x"]
    assert config_loader.remember("y") == ["y"]


def test_describe():
    assert config_loader.describe({"b": 1, "a": 2}) == "2 settings: ['a', 'b']"
