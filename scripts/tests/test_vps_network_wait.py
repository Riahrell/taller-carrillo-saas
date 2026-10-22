import unittest

from scripts.configure_vps_network_wait import validate_static


class StaticNetworkTests(unittest.TestCase):
    def test_requires_static_addresses_and_explicit_default_routes(self):
        interface = {
            "addresses": ["192.0.2.4/24", "2001:db8::4/64"],
            "routes": [{"to": "0.0.0.0/0"}, {"to": "::/0"}],
        }
        validate_static({"network": {"ethernets": {"eth0": interface}}})
        for field, value in [
            ("dhcp4", True),
            ("dhcp6", True),
            ("addresses", ["192.0.2.4/24"]),
            ("routes", []),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_static(
                    {"network": {"ethernets": {"eth0": {**interface, field: value}}}}
                )
