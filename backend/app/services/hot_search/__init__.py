"""Hot Jobs Search package.

The full pipeline is split across:
  - ``types``: dataclasses (CompanyCandidate, CompanyHit, SearchEvent)
  - ``orchestration``: the main search loop (run_hot_company_search)
  - ``app.services.hot_company_search``: discovery + evaluation helpers
    (still the largest module — splitting it further is a follow-up that
    needs test coverage on the picker / verifier first).

Backward-compat: callers should still import from
``app.services.hot_company_search`` for now; that module re-exports the
orchestration entry point and the types from this package.
"""
