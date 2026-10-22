"""Private raw-byte images; authenticate before bounded body consumption."""

import asyncio
from threading import BoundedSemaphore
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.modules.catalog.images import MAX_INPUT_BYTES
from app.modules.catalog.routes import NO_STORE, _expected_write, _response, _token

router = APIRouter(prefix="/catalog/products", tags=["private product images"])
_UPLOAD_SLOTS = BoundedSemaphore(2)


def _service(request: Request):
    service = request.app.state.product_images
    if service is None:
        raise HTTPException(503, "Images unavailable", headers=NO_STORE)
    return service


def _key(request: Request) -> UUID:
    values = request.headers.getlist("idempotency-key")
    if not values:
        raise HTTPException(428, "Precondition required", headers=NO_STORE)
    try:
        if len(values) != 1 or str(UUID(values[0])) != values[0]:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        raise HTTPException(422, "Invalid request", headers=NO_STORE) from None


@router.post("/{product_id}/image")
async def replace_image(product_id: UUID, request: Request) -> JSONResponse:
    token, expected = _token(request), _expected_write(request)
    operation = _key(request)
    service = _service(request)
    if not _UPLOAD_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Image upload busy", headers=NO_STORE)
    try:
        await run_in_threadpool(
            service.authorize_upload,
            token,
            expected_actor=expected,
            product_id=product_id,
        )
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_INPUT_BYTES:
                        raise HTTPException(413, "Image too large", headers=NO_STORE)
                    body.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, "Upload timed out", headers=NO_STORE) from None
        length = request.headers.get("content-length")
        if length is not None and len(body) != int(length):
            raise HTTPException(400, "Invalid request", headers=NO_STORE)
        result = await run_in_threadpool(
            service.replace,
            token,
            expected_actor=expected,
            product_id=product_id,
            operation_key=operation,
            content=bytes(body),
        )
        return _response(result)
    finally:
        _UPLOAD_SLOTS.release()


@router.get("/{product_id}/image/thumbnail")
def read_thumbnail(product_id: UUID, request: Request) -> Response:
    return _read_image(product_id, request, thumbnail=True)


@router.get("/{product_id}/image")
def read_image(product_id: UUID, request: Request) -> Response:
    return _read_image(product_id, request)


def _read_image(product_id: UUID, request: Request, *, thumbnail=False) -> Response:
    request.app.state.http_security.verify_browser_read(request.headers)
    token, expected = _token(request), _expected_write(request)
    result = _service(request).read(
        token, expected_actor=expected, product_id=product_id, thumbnail=thumbnail
    )
    headers = NO_STORE | {
        "X-Access-Membership": str(expected),
        "X-Content-Type-Options": "nosniff",
    }
    if result is None:
        return Response(status_code=204, headers=headers)
    image, content = result
    return Response(
        content,
        media_type=image.content_type,
        headers=headers
        | {
            "X-Image-Id": str(image.id),
            "Content-Disposition": 'inline; filename="product.webp"'
            if image.content_type == "image/webp"
            else 'inline; filename="product.png"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
