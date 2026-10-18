"""A fresh runtime process must resolve audit FKs without test-only imports."""

import subprocess
import sys
from pathlib import Path


def test_access_import_registers_all_referenced_models_in_fresh_process():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.modules.access.models import AuditEvent; "
            "from app.core.database import Base; "
            "tables = {table.name for table in Base.metadata.sorted_tables}; "
            "assert {'audit_events', 'products', 'categories'} <= tables",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
