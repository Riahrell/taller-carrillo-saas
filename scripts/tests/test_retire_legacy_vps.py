import copy
import tempfile
import unittest
from pathlib import Path

from scripts.retire_legacy_vps import (
    CONTAINERS,
    IMAGES,
    NETWORKS,
    VOLUMES,
    validate_directory,
    validate_inventory,
)


def fixture():
    containers = [
        {
            "Name": "/" + name,
            "Id": identity,
            "Config": {"Labels": {"com.docker.compose.project": project}},
            "Mounts": [{"Name": "taller-carrillo_postgres_data"}]
            if name == "saas_postgres"
            else [],
        }
        for name, (identity, project) in CONTAINERS.items()
    ]
    volumes = [
        {
            "Name": name,
            "Driver": "local",
            "Options": None,
            "Labels": {"com.docker.compose.project": "taller-carrillo"},
        }
        for name in VOLUMES
    ]
    return containers, volumes, NETWORKS | {"bridge", "host", "none"}, dict(IMAGES)


class LegacyRetirementTests(unittest.TestCase):
    def test_exact_inventory_is_accepted(self):
        validate_inventory(*fixture())

    def test_unknown_resources_or_replaced_identity_are_rejected(self):
        for change in (
            "container",
            "volume",
            "network",
            "image",
            "identity",
            "mount",
            "remote-volume",
        ):
            with self.subTest(change=change):
                containers, volumes, networks, images = copy.deepcopy(fixture())
                if change == "container":
                    containers.append({"Name": "/new-project"})
                elif change == "volume":
                    volumes.append({"Name": "new-data"})
                elif change == "network":
                    networks.add("new-project")
                elif change == "image":
                    images[next(iter(images))] = "different-image"
                elif change == "identity":
                    containers[0]["Id"] = "replacement"
                elif change == "mount":
                    containers[0]["Mounts"] = [{"Name": "unknown-data"}]
                else:
                    volumes[0]["Options"] = {"type": "nfs"}
                with self.assertRaises(ValueError):
                    validate_inventory(containers, volumes, networks, images)

    def test_directory_symlink_and_nested_mount_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "app"
            folder.mkdir()
            validate_directory(folder, [])
            link = Path(directory) / "link"
            link.symlink_to(folder, target_is_directory=True)
            with self.assertRaises(ValueError):
                validate_directory(link, [])
            with self.assertRaises(ValueError):
                validate_directory(folder, [str(folder / "uploads")])
