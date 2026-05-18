"""Canonical registry of ATS scrapers.

Both the regular scrape runner (``runner.run_scrape``) and the hot-search
ad-hoc evaluator (``hot_company_search._evaluate_company``) dispatch to the
same scrapers. Keeping the registry here removes the previous duplication
where ``runner.py`` had its own ``SCRAPERS = [...]`` list and
``company_discovery.py`` had its own ``_SCRAPER_MAP = {...}`` dict.
"""

from app.models.companies import Company
from app.scrapers.ashby import AshbyScraper
from app.scrapers.eightfold import EightfoldScraper
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.lever import LeverScraper

# Keyed by JobSource enum value / ATS slug discriminator. The order is the
# preference order used by hot-search slug probing (greenhouse → lever →
# ashby → eightfold). Eightfold is intentionally not in that probe loop —
# it's enterprise and rarely produces relevant candidates for our use case
# — but it's included here for the regular scraper runner.
SCRAPERS_BY_ATS = {
    "greenhouse": GreenhouseScraper(),
    "lever": LeverScraper(),
    "ashby": AshbyScraper(),
    "eightfold": EightfoldScraper(),
}


def make_temp_company(ats: str, slug: str) -> Company:
    """Build a non-persisted Company with the right slug field set.

    Used by hot-search code paths that need to drive an ATS scraper for an
    `(ats, slug)` pair without first creating a DB record.
    """
    company = Company(name=slug)
    if ats == "greenhouse":
        company.greenhouse_slug = slug
    elif ats == "lever":
        company.lever_slug = slug
    elif ats == "ashby":
        company.ashby_slug = slug
    elif ats == "eightfold":
        company.eightfold_slug = slug
    return company


__all__ = ["SCRAPERS_BY_ATS", "make_temp_company"]
