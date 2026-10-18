"""Normalized usage metadata extracted from Pydantic model responses."""

from decimal import Decimal, InvalidOperation

from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage


ESTIMATED_USAGE_DETAIL = "bifrost_usage_estimated"


def provider_reported_usage(response: ModelResponse) -> RequestUsage:
    """Return only token counts measured by the upstream provider.

    ``ObservedModel`` may replace a completely empty provider report with a
    conservative estimate so Pydantic AI can enforce the run budget.  Those
    estimates must not flow into durable usage/cost accounting as if the
    provider measured them.
    """

    if response.usage.details.get(ESTIMATED_USAGE_DETAIL):
        return RequestUsage()
    return response.usage


def provider_reported_cost(response: ModelResponse) -> Decimal | None:
    """Return exact provider cost when the adapter preserved one."""

    raw = (response.provider_details or {}).get("cost")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
