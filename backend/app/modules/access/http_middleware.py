"""Pure ASGI guard for writes; CSRF proves browser intent, not authorization."""

import json
import re

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.modules.access.http_security import HttpSecurity, RequestRejected

MAX_JSON_BYTES = 8192
_IMAGE_UPLOAD = re.compile(
    r"/catalog/products/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/image"
)


class HttpSecurityMiddleware:
    def __init__(self, app: ASGIApp, *, policy: HttpSecurity) -> None:
        self.app, self.policy = app, policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        try:
            self.policy.verify_request(headers)
        except RequestRejected:
            await self._reject(scope, receive, send, 403)
            return
        content_types = headers.getlist("content-type")
        binary = scope["method"] == "POST" and _IMAGE_UPLOAD.fullmatch(scope["path"])
        maximum = 8 * 1024 * 1024 if binary else MAX_JSON_BYTES
        if scope["method"] == "POST" and scope["path"] == "/purchases":
            maximum = 32768  # Up to 100 validated lines; other JSON stays at 8 KiB.
        if len(content_types) != 1 or content_types[0].split(";", 1)[
            0
        ].strip().lower() != (
            "application/octet-stream" if binary else "application/json"
        ):
            await self._reject(scope, receive, send, 415)
            return
        lengths = headers.getlist("content-length")
        if lengths and (
            len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
        ):
            await self._reject(scope, receive, send, 400)
            return
        if lengths and (len(lengths[0]) > 10 or int(lengths[0]) > maximum):
            await self._reject(scope, receive, send, 413)
            return
        if binary:
            # The authenticated image handler bounds stream bytes, concurrency
            # and total body time before materializing or decoding the file.
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > maximum:
                await self._reject(scope, receive, send, 413)
                return
            if not message.get("more_body", False):
                break
        if lengths and len(body) != int(lengths[0]):
            await self._reject(scope, receive, send, 400)
            return
        try:
            json.loads(
                bytes(body).decode("utf-8"),
                parse_constant=self._invalid_constant,
                object_pairs_hook=self._unique_object,
            )
        except (ValueError, UnicodeError, RecursionError):
            await self._reject(scope, receive, send, 400)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, replay, no_store)

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous JSON")
            result[key] = value
        return result

    @staticmethod
    def _invalid_constant(_value: str) -> None:
        raise ValueError("Invalid JSON")

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        response = JSONResponse(
            {"detail": "Request rejected"},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
