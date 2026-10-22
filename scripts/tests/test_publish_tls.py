import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.publish_tls import publish
from scripts.render_tls_proxy import render


class CertificateTests(unittest.TestCase):
    def test_matching_pair_is_published_and_bad_hostname_preserves_current(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cert, key = folder / "source.pem", folder / "key.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "10",
                    "-subj",
                    "/CN=panel.example.com",
                    "-addext",
                    "subjectAltName=DNS:panel.example.com",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            current = publish(
                folder / "published", cert, key, "panel.example.com", ca_file=cert
            )
            before = current.resolve()
            self.assertEqual(
                (current / "fullchain.pem").read_bytes(), cert.read_bytes()
            )
            self.assertEqual(
                (current / "private-key.pem").read_bytes(), key.read_bytes()
            )
            with self.assertRaises(ValueError):
                publish(
                    folder / "published", cert, key, "wrong.example.com", ca_file=cert
                )
            self.assertEqual(current.resolve(), before)

    def test_acme_only_added_when_requested(self):
        self.assertNotIn("/.well-known/acme-challenge/", render("panel.example.com"))
        text = render("panel.example.com", acme_webroot=True)
        self.assertIn("location ^~ /.well-known/acme-challenge/", text)
        self.assertIn("try_files $uri =404;", text)
        self.assertIn("return 308 https://panel.example.com$request_uri;", text)
