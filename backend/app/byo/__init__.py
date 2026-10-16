"""Bring your own repo: scan a public GitHub repo or a pasted package.json against the qix incident.

Each scan is loaded into its own isolated FalkorDB graph (`byo:<tenant>:<hash>`, listed only in that tenant) with the same schema as `fleet:acme`,
plus a copy of the incident's Advisory/Maintainer nodes, and answered with the same exposure tool.
"""
