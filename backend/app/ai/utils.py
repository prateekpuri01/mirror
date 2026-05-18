"""Shared utilities for AI modules."""

import re


def employer_key(employer_name: str) -> str:
    """Generate a stable slug key from an employer name.

    Examples:
        'The RAND Corporation' → 'rand_corporation'
        'FINRA' → 'finra'
        'UCLA Physics' → 'ucla_physics'
    """
    name = re.sub(r"^the\s+", "", employer_name.lower().strip())
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", "_", name.strip())
