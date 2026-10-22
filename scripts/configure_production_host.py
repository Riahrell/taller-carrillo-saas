"""First install only: private environment, TLS renewal and encrypted backup units."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.private_files import write_mount_secret
from scripts.publish_tls import publish
from scripts.render_tls_proxy import render
from scripts.setup_local import generate

ROOT = Path("/etc/taller-carrillo")
APP = Path("/opt/taller-carrillo")
DOMAIN = "panel.taller-carrillo.com"


def private_write(path, payload, mode=0o600):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb"
    ) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--recipient", required=True)
    args = parser.parse_args()
    release = args.release.resolve()
    if (
        os.geteuid() != 0
        or release.parent != APP / "releases"
        or not re.fullmatch("[0-9a-f]{40}", release.name)
        or not re.fullmatch("[0-9A-F]{40}", args.recipient)
    ):
        parser.exit(2, "Explicit root release and public fingerprint required.\n")
    incoming = ROOT / "incoming"
    if (ROOT / "production").exists() or (APP / "current").exists():
        parser.exit(1, "First-install configuration exists; nothing overwritten.\n")
    # These inputs contain credentials. Never log or pass their contents in argv.
    required = [
        "images.env",
        "backup-storage.env",
        "smtp.json",
        "smtp_password",
        "recovery-public.asc",
    ]
    if any(not (incoming / name).is_file() for name in required):
        parser.exit(1, "Private provisioning inputs missing.\n")
    subprocess.run(["gpg", "--version"], check=True, capture_output=True)
    env = generate("production", base=ROOT)
    runtime = ROOT / "runtime-secrets"
    runtime.mkdir(mode=0o700)
    for source, target in [
        ("images.env", "product_images"),
        ("smtp.json", "smtp_config"),
        ("smtp_password", "smtp_password"),
    ]:
        write_mount_secret(runtime / target, (incoming / source).read_text())
    private_write(
        ROOT / "backup-storage.env", (incoming / "backup-storage.env").read_bytes()
    )
    acme = Path("/var/lib/taller-carrillo/acme")
    acme.mkdir(mode=0o755, parents=True, exist_ok=True)
    write_mount_secret(ROOT / "nginx.conf", render(DOMAIN, acme_webroot=True))
    tls = publish(
        ROOT / "tls",
        Path("/etc/letsencrypt/live") / DOMAIN / "fullchain.pem",
        Path("/etc/letsencrypt/live") / DOMAIN / "privkey.pem",
        DOMAIN,
    )
    version = release.name[:12]
    extra = {
        "BACKEND_IMAGE": "taller-carrillo-backend:" + version,
        "FRONTEND_IMAGE": "taller-carrillo-frontend:" + version,
        "POSTGRES_IMAGE": "taller-carrillo-postgres:" + version,
        "PROXY_IMAGE": "taller-carrillo-proxy:" + version,
        "TLS_PROXY_CONFIG_FILE": str(ROOT / "nginx.conf"),
        "TLS_CERTIFICATE_FILE": str(tls / "fullchain.pem"),
        "TLS_PRIVATE_KEY_FILE": str(tls / "private-key.pem"),
        "ACME_WEBROOT_DIR": str(acme),
        "S3_CONFIG_FILE": str(runtime / "product_images"),
        "SMTP_CONFIG_FILE": str(runtime / "smtp_config"),
        "SMTP_PASSWORD_FILE": str(runtime / "smtp_password"),
    }
    text = env.read_text().replace(
        "PUBLIC_ORIGIN=http://localhost:8080", "PUBLIC_ORIGIN=https://" + DOMAIN
    )
    env.write_text(text + "".join(f"{key}={value}\n" for key, value in extra.items()))
    (APP / "current").symlink_to(release, target_is_directory=True)
    wrapper = """#!/bin/sh
set -eu
cd /opt/taller-carrillo/current
exec docker compose --project-name carrillo-production \
  --env-file /etc/taller-carrillo/production/compose.env \
  -f compose.yaml -f compose.images.yaml -f compose.identity.yaml \
  -f compose.production.yaml "$@"
"""
    private_write(
        Path("/usr/local/sbin/taller-carrillo-compose"), wrapper.encode(), 0o750
    )
    hook = f"""#!/bin/sh
set -eu
case " ${{RENEWED_DOMAINS:-}} " in *" {DOMAIN} "*) ;; *) exit 0 ;; esac
cd /opt/taller-carrillo/current
python3 -m scripts.publish_tls --hostname {DOMAIN} \
  --directory /etc/taller-carrillo/tls \
  --fullchain "$RENEWED_LINEAGE/fullchain.pem" \
  --private-key "$RENEWED_LINEAGE/privkey.pem"
/usr/local/sbin/taller-carrillo-compose up -d --no-deps --force-recreate proxy
"""
    hookpath = Path("/etc/letsencrypt/renewal-hooks/deploy/taller-carrillo")
    hookpath.parent.mkdir(parents=True, exist_ok=True)
    private_write(hookpath, hook.encode(), 0o750)
    keyring = ROOT / "backup-keyring"
    keyring.mkdir(mode=0o700)
    subprocess.run(
        [
            "gpg",
            "--homedir",
            str(keyring),
            "--batch",
            "--quiet",
            "--import",
            str(incoming / "recovery-public.asc"),
        ],
        capture_output=True,
        check=True,
    )
    listed = subprocess.run(
        [
            "gpg",
            "--homedir",
            str(keyring),
            "--with-colons",
            "--list-keys",
            args.recipient,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if args.recipient not in [
        x.split(":")[9] for x in listed.splitlines() if x.startswith("fpr:")
    ]:
        raise ValueError("Imported recovery key does not match fingerprint")
    backup = {
        "directory": "/var/backups/taller-carrillo-production",
        "compose_wrapper": "/usr/local/sbin/taller-carrillo-compose",
        "recipient": args.recipient,
        "gnupg_home": str(keyring),
        "database": "carrillo_prod",
        "storage_config": str(ROOT / "backup-storage.env"),
        "backup_bucket": "taller-carrillo-restauracion",
        "images_bucket": "taller-carrillo-imagenes",
        "identity_keys": str(env.parent / "secrets/identity_keys"),
        "csrf_key": str(env.parent / "secrets/csrf_signing_key"),
        "release_path": str(APP / "current"),
    }
    private_write(ROOT / "backup.json", json.dumps(backup).encode())
    units = {
        "taller-carrillo.service": """[Unit]
Description=Taller Carrillo application
Requires=docker.service
Wants=network-online.target
After=docker.service network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/taller-carrillo-compose up -d --wait --wait-timeout 180
ExecStop=/usr/local/sbin/taller-carrillo-compose stop --timeout 60
TimeoutStartSec=300
TimeoutStopSec=180
[Install]
WantedBy=multi-user.target
""",
        "taller-carrillo-backup.service": """[Unit]
Description=Encrypted inventory backup and private off-VPS copy
Requires=docker.service
After=docker.service taller-carrillo.service
[Service]
Type=oneshot
WorkingDirectory=/opt/taller-carrillo/current
ExecStart=/usr/bin/python3 -m scripts.production_backup \
  --config /etc/taller-carrillo/backup.json
UMask=0077
TimeoutStartSec=3600
""",
        "taller-carrillo-backup.timer": """[Unit]
Description=Daily Taller Carrillo backup
[Timer]
OnCalendar=*-*-* 03:00:00 America/El_Salvador
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
""",
    }
    for name, content in units.items():
        private_write(Path("/etc/systemd/system") / name, content.encode(), 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
    print("First-install configuration ready; services not started and no email sent.")


if __name__ == "__main__":
    main()
