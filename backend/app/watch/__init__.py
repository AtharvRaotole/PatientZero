"""Live watch of npm malware: polls OSV for new MAL-* advisories, ingests them into every tenant graph, runs the
exposure check, and records (:WatchEvent) nodes. See core.py, osv.py, api.py and docs/CONTRACT-watch-mcp-bench.md.
"""
