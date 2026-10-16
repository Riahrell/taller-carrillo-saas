"""Generate isolated local credentials without printing or overwriting them."""

import argparse
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate(environment: str) -> Path:
    base = ROOT / ".local"
    base.mkdir(mode=0o700, exist_ok=True)
    destination = base / environment
    # Fail if anything already exists: regenerating would invalidate DB volumes.
    destination.mkdir(mode=0o700, exist_ok=False)
    secret_dir = destination / "secrets"
    secret_dir.mkdir(mode=0o700)
    db_name = "carrillo_test" if environment == "test" else "carrillo_dev"
    credentials = {
        "bootstrap_password": secrets.token_urlsafe(48),
        "owner_password": secrets.token_urlsafe(48),
        "app_password": secrets.token_urlsafe(48),
    }
    credentials["database_url"] = (
        f"postgresql+psycopg://carrillo_app:{credentials['app_password']}@db:5432/{db_name}"
    )
    credentials["migration_database_url"] = (
        f"postgresql+psycopg://carrillo_owner:{credentials['owner_password']}@db:5432/{db_name}"
    )
    for name, value in credentials.items():
        path = secret_dir / name
        # Readable by container UIDs through read-only secret mounts; host parent
        # directories are 0700, so other host users cannot traverse to the files.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "w") as stream:
            stream.write(value + "\n")
    config = destination / "compose.env"
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(
            f"APP_ENV={environment}\nDB_NAME={db_name}\nDB_SECRETS_DIR={secret_dir}\n"
        )
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["development", "test"])
    args = parser.parse_args()
    try:
        config = generate(args.environment)
    except FileExistsError:
        parser.exit(1, "Environment exists; credentials were not overwritten.\n")
    print(f"Local environment created: {config.relative_to(ROOT)} (values hidden)")


if __name__ == "__main__":
    main()
