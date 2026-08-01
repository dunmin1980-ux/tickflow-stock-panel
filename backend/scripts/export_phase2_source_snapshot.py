#!/usr/bin/env python3
"""CLI entry point for the deterministic Phase 2A source snapshot."""

from app.services.phase2_snapshot import main

if __name__ == "__main__":
    raise SystemExit(main())
