# SlideRefine evidence bundles

Release evidence is generated under `evidence/<git-sha>/` by `python -m tools.release_gate`.
The final release gate is not satisfied unless `release-verdict.json` reports `overall: pass`
for the same source SHA.
