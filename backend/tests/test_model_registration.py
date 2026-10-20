"""Production imports must register FK targets without integration-test imports."""

import os
import subprocess
import sys
from pathlib import Path


def test_application_import_resolves_all_model_foreign_keys():
    backend = Path(__file__).resolve().parents[1]
    code = (
        f"import sys; sys.path.insert(0, {str(backend)!r}); "
        "import app.main; from app.core.database import Base; "
        "assert all(fk.column is not None for table in Base.metadata.tables.values() "
        "for fk in table.foreign_keys)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
