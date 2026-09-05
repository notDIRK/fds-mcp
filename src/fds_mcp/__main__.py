"""``python -m fds_mcp`` — same entry point as the ``fds-mcp`` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
