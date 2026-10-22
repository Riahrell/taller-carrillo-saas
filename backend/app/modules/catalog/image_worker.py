"""Private executable entry point; resource limits precede decoder imports."""

import resource
import struct
import sys
from pathlib import Path


def main() -> int:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    # -I ignores PYTHONPATH/cwd; only this application's fixed root is imported.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from app.modules.catalog.images import (
        MAX_INPUT_BYTES,
        InvalidProductImage,
        prepare_product_image,
        prepare_product_variants,
    )

    try:
        content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if sys.argv[1:] == ["--webp"]:
            prepared = prepare_product_variants(content)
            thumb = prepared.thumbnail
            sys.stdout.buffer.write(
                b"TCWEBP1\n"
                + struct.pack(
                    ">6I",
                    len(prepared.content),
                    prepared.width,
                    prepared.height,
                    len(thumb.content),
                    thumb.width,
                    thumb.height,
                )
                + prepared.content
                + thumb.content
            )
        else:
            prepared = prepare_product_image(content)
            sys.stdout.buffer.write(prepared.content)
        sys.stdout.buffer.flush()
    except InvalidProductImage:
        return 2
    except (MemoryError, OSError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
