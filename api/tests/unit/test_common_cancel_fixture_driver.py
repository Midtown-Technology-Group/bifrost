"""Connection authority boundary; no engines, DB or fixture execution."""

import pytest

from tests.helpers.common_cancel_fixture import FixturePreparationError
from tests.helpers.common_cancel_fixture_driver import _url


@pytest.mark.parametrize("scheme", ["postgresql", "postgresql+asyncpg"])
def test_explicit_connection_retains_authority_and_connection_options(
    monkeypatch, scheme
):
    monkeypatch.setenv(
        "FIXTURE_TEST_URL",
        f"{scheme}://wex_core:synthetic%40secret@postgres:5432/bifrost_test?ssl=require",
    )
    result = _url("FIXTURE_TEST_URL", "wex_core")
    assert result.drivername == "postgresql+asyncpg"
    assert result.username == "wex_core"
    assert result.password == "synthetic@secret"
    assert result.host == "postgres"
    assert result.database == "bifrost_test"
    assert result.query == {"ssl": "require"}


@pytest.mark.parametrize(
    "value",
    [
        None,
        "postgresql://wex_core:synthetic@postgres/production",
        "postgresql://bifrost:synthetic@postgres/bifrost_test",
        "postgresql://wex_core@postgres/bifrost_test",
        "postgresql://wex_core:synthetic@/bifrost_test",
        "sqlite:///bifrost_test",
        "invalid-synthetic-private-value",
    ],
)
def test_no_default_dsn_principal_substitution_or_unsanitized_config_errors(
    monkeypatch, value
):
    if value is None:
        monkeypatch.delenv("FIXTURE_TEST_URL", raising=False)
    else:
        monkeypatch.setenv("FIXTURE_TEST_URL", value)
    with pytest.raises(FixturePreparationError) as caught:
        _url("FIXTURE_TEST_URL", "wex_core")
    assert str(caught.value) == "explicit fixture connection is invalid"
    assert caught.value.__suppress_context__
