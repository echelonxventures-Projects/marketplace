#!/usr/bin/env python3
"""Start the local UCOS Marketplace test server.

    python3 server.py                  # 127.0.0.1:8000, seeded demo dataset
    python3 server.py --port 9000      # custom port
    python3 server.py --host 0.0.0.0   # listen on all interfaces
    python3 server.py --no-seed        # empty platform, no demo data

The server is stdlib-only and keeps all state in memory, so restarting it
resets everything and re-seeds the demo dataset. See README_LOCAL_TESTING.md
for the full endpoint list and demo credentials.
"""

from __future__ import annotations

import argparse

from marketplace.web.server import DEFAULT_HOST, DEFAULT_PORT, run_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port")
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="start with an empty platform (no demo dataset or credentials)",
    )
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, seed=not args.no_seed)


if __name__ == "__main__":
    main()
