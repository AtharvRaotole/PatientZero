// Tool: incident_scope (bare package names, e.g. "chalk and debug were compromised"). Added by the app layer.
// Params: $pkgs list of package names. Returns every advisory-listed malicious version of those packages.
// Read-only.

// @query incident_scope_by_packages
MATCH (a:Advisory)-[:AFFECTS]->(v:Version)
WHERE v.pkg IN $pkgs
RETURN v.pkg AS pkg, v.version AS bad_version, a.fixed_version AS clean_version, a.id AS advisory,
       a.ghsa AS ghsa, a.cve AS cve, a.aliases AS aliases, a.url AS url, a.published AS as_of, a.url AS source
ORDER BY pkg
