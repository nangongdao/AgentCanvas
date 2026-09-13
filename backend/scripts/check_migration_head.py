"""Assert ``CURRENT_REVISION`` stays equal to the alembic head revision.

``CURRENT_REVISION`` guards three independent surfaces at once — the
``/readyz`` migration check, the scheduler's startup gate, and the event
relay's startup gate. When a new migration lands without bumping it, every
database already at head reports a revision mismatch and all three silently
stop working, which is exactly how a shipped commit disabled them at once
(documented in the C9 backend audit). This check fails fast instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.migrations import CURRENT_REVISION

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_heads() -> tuple[str, ...]:
    """Return the alembic head revisions from the checked-in script directory."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def evaluate() -> tuple[bool, str]:
    """Return (ok, message) comparing CURRENT_REVISION with the alembic head."""
    heads = alembic_heads()
    if len(heads) != 1:
        return False, f"alembic has multiple heads {heads}; merge them before releasing"
    (head,) = heads
    if head != CURRENT_REVISION:
        return (
            False,
            f"app.db.migrations.CURRENT_REVISION is {CURRENT_REVISION!r} but the alembic "
            f"head is {head!r}; bump CURRENT_REVISION (and CURRENT_TABLES, plus any tests "
            "pinning the old revision) whenever a migration is added",
        )
    return True, f"migration head parity ok: {head}"


def main() -> int:
    ok, message = evaluate()
    print(message, file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
