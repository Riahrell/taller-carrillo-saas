"""Explicit private production environment preparation; never deploys or purchases."""

import argparse
from urllib.parse import urlsplit

from scripts.setup_local import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    value = urlsplit(args.origin)
    if (
        value.scheme != "https"
        or not value.hostname
        or value.path
        or value.query
        or value.fragment
        or value.username
        or value.password
        or value.port is not None
        or args.origin != "https://" + value.hostname
        or value.hostname == "localhost"
    ):
        parser.exit(2, "A canonical HTTPS domain origin is required.\n")
    try:
        path = generate("production")
        data = path.read_text().replace(
            "PUBLIC_ORIGIN=http://localhost:8080", "PUBLIC_ORIGIN=" + args.origin
        )
        path.write_text(data)
    except (OSError, ValueError):
        parser.exit(
            1,
            (
                "Production environment exists or cannot be prepared; "
                "secrets were not overwritten.\n"
            ),
        )
    print(
        "Private production environment prepared. No certificates, "
        "SMTP credentials or deployment created."
    )


if __name__ == "__main__":
    main()
