"""Write a reviewable TLS Nginx config. Certificate paths are secret mounts."""

import argparse
import os
import re
from pathlib import Path


def render(hostname: str) -> str:
    if len(hostname) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError("Invalid hostname")
    template = (
        Path(__file__).resolve().parents[1] / "ops/proxy/nginx.conf"
    ).read_text()
    template = template.replace(
        "listen 8080;",
        "listen 8443 ssl;\n        server_name "
        + hostname
        + ";\n        if ($host != "
        + hostname
        + (
            ") { return 444; }\n        ssl_certificate "
            "/run/secrets/tls_certificate;\n        ssl_certificate_key "
            "/run/secrets/tls_private_key;\n        ssl_protocols TLSv1.2 "
            "TLSv1.3;\n        ssl_session_tickets off;"
        ),
    )
    index = template.index("    server {")
    template = (
        template[:index]
        + """    server {
        listen 127.0.0.1:8081;
        location = /health { proxy_pass http://backend:8000/health/live; }
    }
"""
        + """    server {
        listen 8080;
        server_name """
        + hostname
        + """;
        if ($host != """
        + hostname
        + """) { return 444; }
        location / { return 308 https://"""
        + hostname
        + """$request_uri; }
    }

"""
        + template[index:]
    )
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        value = render(args.hostname)
        with os.fdopen(
            os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444), "w"
        ) as output:
            output.write(value)
    except (OSError, ValueError):
        parser.exit(1, "TLS configuration refused; no existing file overwritten.\n")
    print("TLS proxy configuration created; review before deployment.")


if __name__ == "__main__":
    main()
