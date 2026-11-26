import pytest

from pactor.config.env import (
    ConfigurationError,
    env_bool,
    env_int,
    env_list,
    env_rate_limits,
    env_str,
)


def test_env_str_returns_value_or_default() -> None:
    assert env_str("A", environ={"A": "x"}) == "x"
    assert env_str("A", "d", environ={}) == "d"


def test_env_str_without_default_fails_fast() -> None:
    with pytest.raises(ConfigurationError, match="A"):
        env_str("A", environ={})


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", True), ("YES", True), ("off", False), ("", False)]
)
def test_env_bool_accepts_known_spellings(raw: str, *, expected: bool) -> None:
    assert env_bool("B", default=False, environ={"B": raw}) is expected


def test_env_bool_rejects_ambiguous_values() -> None:
    with pytest.raises(ConfigurationError):
        env_bool("B", default=False, environ={"B": "talvez"})


def test_env_bool_uses_default_when_missing() -> None:
    assert env_bool("B", default=True, environ={}) is True


def test_env_int_parses_and_rejects() -> None:
    assert env_int("N", environ={"N": "42"}) == 42
    with pytest.raises(ConfigurationError):
        env_int("N", environ={"N": "4x"})


def test_env_list_strips_and_drops_empty_items() -> None:
    assert env_list("L", environ={"L": " a, ,b ,"}) == ["a", "b"]


@pytest.mark.parametrize("raw", ["10/m/ip,5/5m/key", "10/m/ip", "3/30s", " 1/1.5h/user , 2/d/key "])
def test_env_rate_limits_accepts_allauth_format(raw: str) -> None:
    assert env_rate_limits("R", "1/m", environ={"R": raw}) == ",".join(
        part.strip() for part in raw.split(",")
    )


@pytest.mark.parametrize("raw", ["", " ", "10/m/ipp", "10/x/ip", "dez/m/ip", "10/m/ip,", "10"])
def test_env_rate_limits_rejects_empty_or_malformed(raw: str) -> None:
    # Vazio desligaria o limite em silêncio; erro de digitação só apareceria no login.
    with pytest.raises(ConfigurationError, match="R"):
        env_rate_limits("R", "1/m", environ={"R": raw})


def test_env_rate_limits_uses_default_when_missing() -> None:
    assert env_rate_limits("R", "10/m/ip,5/5m/key", environ={}) == "10/m/ip,5/5m/key"
