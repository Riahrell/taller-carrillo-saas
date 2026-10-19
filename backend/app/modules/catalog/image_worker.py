"""Private executable entry point; resource limits precede decoder imports."""

import resource
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
    )

    try:
        prepared = prepare_product_image(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        sys.stdout.buffer.write(prepared.content)
        sys.stdout.buffer.flush()
    except InvalidProductImage:
        return 2
    except (MemoryError, OSError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
