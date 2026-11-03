import asyncio
import json
from urllib import error, parse, request


class APIClient:
    def __init__(self, base_url: str = "", timeout: int = 10, headers: dict | None = None):
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if headers:
            self.headers.update(headers)

    def build_url(self, path: str) -> str:
        if not path:
            return self.base_url
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not self.base_url:
            return path
        return f"{self.base_url}{path}"

    def _request_json(self, method: str, path: str, payload: dict | None = None, params: dict | None = None, headers: dict | None = None):
        url = self.build_url(path)

        if params:
            url = f"{url}?{parse.urlencode(params)}"

        request_data = None
        if payload is not None:
            request_data = json.dumps(payload).encode("utf-8")

        final_headers = dict(self.headers)
        if headers:
            final_headers.update(headers)

        req = request.Request(url, data=request_data, headers=final_headers, method=method.upper())

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                response_text = response.read().decode("utf-8")
                return json.loads(response_text) if response_text else None
        except error.HTTPError as exc:
            raise RuntimeError(f"API request failed with status {exc.code}: {exc.reason}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Could not reach API: {exc.reason}") from exc

    async def get_json(self, path: str, params: dict | None = None, headers: dict | None = None):
        return await asyncio.to_thread(self._request_json, "GET", path, None, params, headers)

    async def post_json(self, path: str, payload: dict | None = None, headers: dict | None = None):
        return await asyncio.to_thread(self._request_json, "POST", path, payload, None, headers)

    async def put_json(self, path: str, payload: dict | None = None, headers: dict | None = None):
        return await asyncio.to_thread(self._request_json, "PUT", path, payload, None, headers)
