import pytest

from app.config import ConfigError, Settings, parse_bool, parse_int, require

BASE = {"INVENTORY_URL": "http://inventory:8080/", "API_KEY": "s3cret"}


def test_defaults() -> None:
    s = Settings.from_env(BASE)
    assert s.inventory_url == "http://inventory:8080"
    assert s.feature_discounts is False
    assert s.discount_percent == 0
    assert s.request_timeout_ms == 2000
    assert s.log_level == "INFO"


@pytest.mark.parametrize(
    "raw,expected", [("true", True), ("False", False), (" 1 ", True), ("off", False)]
)
def test_parse_bool_accepts_known_spellings(raw: str, expected: bool) -> None:
    assert parse_bool("X", raw) is expected


@pytest.mark.parametrize("raw", ["ture", "nope", "2"])
def test_parse_bool_rejects_garbage(raw: str) -> None:
    with pytest.raises(ConfigError):
        parse_bool("X", raw)


def test_parse_bool_required_when_no_default() -> None:
    with pytest.raises(ConfigError):
        parse_bool("X", None)


def test_parse_int_rejects_helm_float_rendering() -> None:
    # Helm renders a large int from values.yaml as 1e+06 unless you `| int`.
    with pytest.raises(ConfigError):
        parse_int("X", "1e+06", default=0)


def test_parse_int_minimum() -> None:
    with pytest.raises(ConfigError):
        parse_int("X", "0", default=1, minimum=1)


@pytest.mark.parametrize("raw", [None, "", "  ", "null", "<nil>", "None"])
def test_require_rejects_empty_and_null(raw: str | None) -> None:
    with pytest.raises(ConfigError):
        require("X", raw)


def test_invalid_log_level() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE, "LOG_LEVEL": "loud"})


def test_discount_over_100_rejected() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE, "DISCOUNT_PERCENT": "150"})


def test_missing_api_key() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({"INVENTORY_URL": "http://x"})
