"""Binance Square OpenAPI client.

Python port of Binance's official square-post skill
(github.com/binance/binance-skills-hub, skills/binance/square-post/scripts/lib.mjs).
Same endpoints, headers and upload flow.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

BASE_URL_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BASE_URL_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"
KEY_FILE = Path.home() / ".config" / "binance-square" / "openapi-key"

# Documented error codes from the official skill.
ERROR_HINTS = {
    "220003": "API key not found",
    "220004": "API key expired",
    "220009": "Daily post limit exceeded for OpenAPI (100/day)",
    "220014": "Daily upload limit exceeded (400/day)",
    "20002": "Sensitive words detected",
    "20022": "Sensitive words detected",
    "20013": "Content length is limited",
    "20020": "Content body must not be empty",
    "220011": "Content body must not be empty",
    "30008": "Account or device posting restriction",
    "2000001": "Account or device posting restriction",
    "2000002": "Account or device posting restriction",
}

# Codes where retrying the same content is pointless.
PERMANENT_CODES = {"220003", "220004", "220009", "220014", "20002", "20022", "20013", "20020", "220011",
                   "30008", "2000001", "2000002"}


class SquareError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        hint = ERROR_HINTS.get(code)
        super().__init__(f"Square API error [{code}]: {message}" + (f" ({hint})" if hint else ""))

    @property
    def permanent(self) -> bool:
        return self.code in PERMANENT_CODES


@dataclass
class PublishResult:
    post_id: str | None
    link: str | None


def resolve_api_key() -> str:
    key = os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip()
    if key:
        return key
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise RuntimeError(
        "Missing Square OpenAPI key. Set BINANCE_SQUARE_OPENAPI_KEY or save it to "
        f"{KEY_FILE}. Create one at https://www.binance.com/square/creator-center/home"
    )


def mask_key(key: str) -> str:
    return f"{key[:5]}...{key[-4:]}" if len(key) > 9 else f"{key[:2]}..."


class SquareClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None,
                 poll_interval: float = 3.0, max_polls: int = 10):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=60)
        self.poll_interval = poll_interval
        self.max_polls = max_polls

    def _api(self, endpoint: str, body: dict, base_url: str = BASE_URL_V2):
        r = self.http.post(
            f"{base_url}{endpoint}",
            json=body,
            headers={
                "X-Square-OpenAPI-Key": self.api_key,
                "Content-Type": "application/json",
                "clienttype": "binanceSkill",
            },
        )
        # Official behaviour: /content/add can time out after the post was accepted.
        if endpoint == "/content/add" and r.status_code == 504:
            return {"id": None, "shareLink": None}
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"Square API returned non-JSON response: {r.status_code} {r.text[:300]}")
        if data.get("code") != "000000":
            raise SquareError(str(data.get("code")), str(data.get("message")))
        return data.get("data")

    def upload_image(self, path: str | Path) -> str:
        path = Path(path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        info = self._api("/image/presignedUrl", {"imageName": path.name})
        put = self.http.put(info["presignedUrl"], content=path.read_bytes(), headers={"Content-Type": content_type})
        if put.status_code >= 300:
            raise RuntimeError(f"Image upload failed: {put.status_code} {put.text[:200]}")
        for _ in range(self.max_polls):
            status = self._api("/image/imageStatus", {"fileTicket": info["fileTicket"]})
            if status.get("status") == 1:
                return status["imageUrl"]
            if status.get("status") == 2:
                raise RuntimeError(f"Image processing failed: {status.get('failedReason')}")
            time.sleep(self.poll_interval)
        raise RuntimeError("Image processing timed out")

    def publish(self, text: str, title: str | None = None, image_path: str | None = None) -> PublishResult:
        """Short post (contentType=1) or article (contentType=2 when title is set).

        Short post: image_path becomes a single-item imageList.
        Article: image_path becomes the cover.
        """
        body: dict = {"contentType": 2 if title else 1, "bodyTextOnly": text}
        if title:
            body["title"] = title
        if image_path:
            url = self.upload_image(image_path)
            if title:
                body["cover"] = url
            else:
                body["imageList"] = [url]
        data = self._api("/content/add", body, base_url=BASE_URL_V1) or {}
        return PublishResult(post_id=str(data["id"]) if data.get("id") else None, link=data.get("shareLink"))
