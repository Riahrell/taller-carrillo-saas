import hashlib
from uuid import uuid4

import pytest

from app.modules.catalog.image_storage import ImageStorageConflict
from app.tools.image_archive import (
    Entry,
    export_archive,
    restore_archive,
    verify_archive,
)


class Storage:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = 0

    def read(self, reference):
        return self.objects[reference.key]

    def put(self, reference, image):
        self.puts += 1
        if reference.key in self.objects:
            raise ImageStorageConflict()
        self.objects[reference.key] = image.content


def entries():
    base = f"product-images/{uuid4()}/{uuid4()}/{uuid4()}"
    result = []
    objects = {}
    for suffix, payload in [
        (".webp", b"synthetic detail"),
        (".thumb.webp", b"synthetic thumbnail"),
    ]:
        entry = Entry(
            key=base + suffix,
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type="image/webp",
            width=100,
            height=75,
        )
        result.append(entry)
        objects[entry.key] = payload
    return result, objects


def test_export_verify_restore_and_replay(tmp_path):
    refs, objects = entries()
    folder = tmp_path / "archive"
    assert export_archive(folder, refs, Storage(objects)) == 2
    assert verify_archive(folder).entries == refs
    target = Storage()
    assert restore_archive(folder, target) == 2
    assert target.objects == objects
    assert restore_archive(folder, target) == 2
    assert target.objects == objects
    assert folder.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in folder.iterdir())


def test_corrupt_last_object_prevents_all_restore_writes(tmp_path):
    refs, objects = entries()
    folder = tmp_path / "archive"
    export_archive(folder, refs, Storage(objects))
    (folder / "1.blob").write_bytes(b"damaged")
    target = Storage()
    with pytest.raises(ValueError, match="integrity"):
        restore_archive(folder, target)
    assert target.puts == 0


def test_conflicting_existing_object_is_not_overwritten(tmp_path):
    refs, objects = entries()
    folder = tmp_path / "archive"
    export_archive(folder, refs, Storage(objects))
    target = Storage({refs[0].key: b"other object"})
    with pytest.raises(ValueError, match="mismatch"):
        restore_archive(folder, target)
    assert target.objects == {refs[0].key: b"other object"}


def test_failed_export_has_no_completion_manifest(tmp_path):
    refs, objects = entries()
    del objects[refs[-1].key]
    folder = tmp_path / "archive"
    with pytest.raises(KeyError):
        export_archive(folder, refs, Storage(objects))
    assert not (folder / "manifest.json").exists()
    with pytest.raises(FileExistsError):
        export_archive(folder, refs, Storage(objects))


@pytest.mark.parametrize("unsafe", ["symlink", "public"])
def test_reject_unsafe_files_before_restore(tmp_path, unsafe):
    refs, objects = entries()
    folder = tmp_path / "archive"
    export_archive(folder, refs, Storage(objects))
    blob = folder / "0.blob"
    if unsafe == "symlink":
        blob.rename(folder / "moved")
        blob.symlink_to(folder / "moved")
    else:
        blob.chmod(0o644)
    target = Storage()
    with pytest.raises((ValueError, OSError)):
        restore_archive(folder, target)
    assert target.puts == 0


def test_reject_duplicate_keys_and_traversal(tmp_path):
    refs, objects = entries()
    folder = tmp_path / "archive"
    export_archive(folder, [refs[0], refs[0]], Storage(objects))
    with pytest.raises(ValueError, match="Duplicate"):
        verify_archive(folder)
    with pytest.raises(ValueError):
        Entry(**{**refs[0].model_dump(), "key": "../secret"})
