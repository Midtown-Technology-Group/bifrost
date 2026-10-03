"""A stored connection declaration must not bypass export's secret scrub."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.services.solutions.capture import SolutionCaptureService


async def test_persisted_connection_templates_are_scrubbed_without_mutation():
    template = {
        "name": "HaloPSA",
        "client_id": "secret-client",
        "config_values": {"token": "secret-token"},
        "config_schema": [
            {"key": "base_url", "type": "string", "default_value": "secret-default"}
        ],
        "oauth": {
            "provider_name": "HaloPSA",
            "scopes": ["read"],
            "client_secret": "secret-oauth",
        },
    }
    original = deepcopy(template)
    rows = [SimpleNamespace(integration_name="HaloPSA", template=template, position=0)]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: rows)
    )
    result = await SolutionCaptureService(db)._connection_entries(
        uuid4(), source_files={"run.py": "pass"}
    )
    encoded = json.dumps(result)
    assert "secret-" not in encoded
    assert result[0]["template"]["oauth"] == {
        "provider_name": "HaloPSA",
        "scopes": ["read"],
    }
    assert result[0]["template"]["config_schema"] == [
        {"key": "base_url", "type": "string"}
    ]
    assert template == original
    db.add.assert_not_called()
