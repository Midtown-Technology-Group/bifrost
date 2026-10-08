"""Old operator commands cannot recover authority after Live retirement."""

from bifrost import cli


def test_promote_command_is_rejected_before_client_lookup(monkeypatch, capsys):
    def unexpected_client(*_args, **_kwargs):
        raise AssertionError("Retired promotion must not authenticate or call an API")

    monkeypatch.setattr(cli, "_check_cli_version", lambda: None)
    monkeypatch.setattr(cli.BifrostClient, "get_instance", unexpected_client)
    assert cli.main(["promote", "activate", "old-release-id"]) == 1
    assert "Unknown command: promote" in capsys.readouterr().err
