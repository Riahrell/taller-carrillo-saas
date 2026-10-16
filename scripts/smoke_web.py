"""Read-only smoke test for the frontend and API behind the same proxy."""

import argparse
import json
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.language: str | None = None
        self.assets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "html":
            self.language = attributes.get("lang")
        asset = attributes.get("src" if tag == "script" else "href")
        if tag in {"script", "link"} and asset and asset.startswith("/_next/static/"):
            self.assets.add(asset)


def read(
    base: str,
    path: str,
    expected: int = 200,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
):
    request = Request(
        base + path,
        method=method,
        headers=headers or {},
        data=data if data is not None else (b"{}" if method == "POST" else None),
    )
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise RuntimeError(f"Unexpected HTTP status for {path}: {response.status}")
        return response.read(1_000_000), response.headers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        parser.error("Use an origin URL without credentials, query, or path")
    base = args.base_url.rstrip("/")
    body, headers = read(base, "/")
    assert "text/html" in headers.get("Content-Type", "")
    page = Page()
    page.feed(body.decode())
    assert page.language == "es"
    assert "Acceso aún no disponible" in body.decode()
    assert page.assets, "No frontend static assets found"
    for asset in sorted(page.assets):
        read(base, asset, method="HEAD")
    health, headers = read(base, "/api/health/live")
    assert json.loads(health) == {"status": "ok"}
    assert "application/json" in headers.get("Content-Type", "")
    assert headers.get("X-Content-Type-Options") == "nosniff"
    missing, _ = read(base, "/pagina-inexistente", expected=404)
    assert "No encontramos esta página" in missing.decode()
    read(base, "/health/live", expected=404)
    read(base, "/api/docs", expected=404)
    rejected, rejected_headers = read(
        base,
        "/api/health/live",
        expected=403,
        method="POST",
        headers={
            "Origin": "https://untrusted.example",
            "Content-Type": "application/json",
        },
    )
    assert json.loads(rejected) == {"detail": "Request rejected"}
    assert rejected_headers.get("Cache-Control") == "no-store"
    oversized, oversized_headers = read(
        base,
        "/api/health/live",
        expected=413,
        method="POST",
        data=b" " * 8193,
        headers={"Origin": base, "Content-Type": "application/json"},
    )
    assert json.loads(oversized) == {"detail": "Request rejected"}
    assert oversized_headers.get("Cache-Control") == "no-store"
    print(
        f"Smoke OK: HTML español, {len(page.assets)} assets, API del mismo origen, "
        "404 de interfaz/API, cabeceras y rechazo de escritura sin origen/CSRF válidos."
    )


if __name__ == "__main__":
    main()
