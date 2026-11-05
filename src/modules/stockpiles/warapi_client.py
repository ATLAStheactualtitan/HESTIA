"""Small async WarAPI client with conditional ETag caching."""

import re
import time
from urllib.parse import quote

import aiohttp


class WarAPIClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._cache: dict[str, tuple[float, str | None, object]] = {}

    async def get_json(self, path: str):
        cached = self._cache.get(path)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[2]

        headers = {}
        if cached and cached[1]:
            headers["If-None-Match"] = cached[1]

        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(f"{self.base_url}{path}", headers=headers) as response:
                if response.status == 304 and cached:
                    data = cached[2]
                else:
                    response.raise_for_status()
                    data = await response.json()

                max_age = 5.0
                cache_control = response.headers.get("Cache-Control", "")
                cache_directives = cache_control.lower()
                match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control, re.IGNORECASE)
                if match:
                    max_age = max(0.0, float(match.group(1)))
                if "no-cache" in cache_directives or "no-store" in cache_directives:
                    max_age = 0.0
                should_store = "no-store" not in cache_directives
                etag = response.headers.get("ETag") or (cached[1] if cached else None)

        if should_store:
            self._cache[path] = (time.monotonic() + max_age, etag, data)
        else:
            self._cache.pop(path, None)
        return data

    async def get_war(self):
        return await self.get_json("/worldconquest/war")

    async def get_maps(self):
        return await self.get_json("/worldconquest/maps")

    async def get_war_report(self, map_name: str):
        return await self.get_json(
            f"/worldconquest/warReport/{quote(map_name, safe='')}"
        )