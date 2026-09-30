"""Read immutable source resources from the current worker's deployment."""

from urllib.parse import quote

from .client import get_client, raise_for_status_with_detail
from ._local_resources import current_local_resources


class resources:
    """Deployment source bytes. Operational files continue to use ``files``."""

    @staticmethod
    async def read_bytes(path: str) -> bytes:
        """Read a reviewed resource pinned when this workflow was accepted.

        The API derives the Solution, deployment and organization from the
        signed active attempt. No deployment selector or Root fallback exists.
        CLI local runs instead use an explicit ``--resource-recipe`` to read
        declared checkout bytes, including dirty edits, without HTTP fallback.
        """
        if (not path or path.startswith("/") or "\\" in path or ":" in path
                or "\0" in path or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise ValueError("Expected a normalized deployment resource path")
        local = current_local_resources()
        if local is not None:
            return local.read(path)
        response = await get_client().get(f"/api/sdk/resources/{quote(path, safe='/')}")
        raise_for_status_with_detail(response)
        return response.content

    @staticmethod
    async def read(path: str) -> str:
        """Read a reviewed UTF-8 source resource from the accepted deployment."""
        return (await resources.read_bytes(path)).decode("utf-8")
