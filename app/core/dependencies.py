from collections.abc import AsyncGenerator

from httpx import AsyncClient, Limits, Timeout


async def get_http_client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        timeout=Timeout(30.0),
        limits=Limits(max_keepalive_connections=20, max_connections=100),
    ) as client:
        yield client
