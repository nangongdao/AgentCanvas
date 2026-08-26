"""Generate and verify checked-in AgentCanvas public contract artifacts."""

from __future__ import annotations

import argparse

from app.contracts import (
    check_contract_files,
    initialize_contract_baseline,
    write_contract_files,
)
from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check", "init-baseline"))
    args = parser.parse_args()
    app = create_app()
    if args.command == "generate":
        write_contract_files(app)
    elif args.command == "init-baseline":
        initialize_contract_baseline(app)
    else:
        check_contract_files(app)


if __name__ == "__main__":
    main()
