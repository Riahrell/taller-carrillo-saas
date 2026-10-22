"""Check generated TLS Nginx syntax as UID 101 using disposable certificates."""

import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from scripts.ci import ROOT, Pipeline
from scripts.render_tls_proxy import render


def validate(pipeline, folder):
    config = json.loads(
        pipeline.compose("config", "--format", "json", capture=True).stdout
    )
    image = config["services"]["proxy"]["image"]
    certificate, key = folder / "certificate.pem", folder / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=tls-probe.example.com",
            "-addext",
            "subjectAltName=DNS:tls-probe.example.com",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        timeout=30,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proxy = folder / "nginx.conf"
    proxy.write_text(render("tls-probe.example.com", acme_webroot=True))
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--user",
        "101:101",
        "--tmpfs",
        "/tmp",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--add-host",
        "backend:127.0.0.1",
        "--add-host",
        "frontend:127.0.0.1",
    ]
    for source, target in (
        (proxy, "/etc/nginx/nginx.conf"),
        (certificate, "/run/secrets/tls_certificate"),
        (key, "/run/secrets/tls_private_key"),
    ):
        source.chmod(0o444)
        arguments.extend(
            ["--mount", f"type=bind,source={source},target={target},readonly"]
        )
    pipeline.run([*arguments, "--entrypoint", "nginx", image, "-t"], timeout=60)


def main():
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ci-tls-", dir=local) as directory:
        folder = Path(directory)
        pipeline = Pipeline(ROOT, folder, "carrillo-ci-" + uuid.uuid4().hex[:12])
        try:
            pipeline.validate_isolation()
            pipeline.compose("build", "proxy", timeout=600)
            validate(pipeline, folder)
        finally:
            pipeline.cleanup()
    print(
        "TLS proxy syntax and UID 101 file access passed; "
        "public chain/renewal remain unverified."
    )


if __name__ == "__main__":
    main()
