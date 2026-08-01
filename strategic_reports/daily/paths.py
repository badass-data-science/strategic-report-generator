"""
Packaged-data path helpers.

RSS feed configs ship as package data (see pyproject.toml's
[tool.setuptools.package-data]), so the default --data-dir must resolve
relative to the installed package, not the current working directory or
STRATEGIC_REPORTS_HOME — those anchor runtime output (--output-dir,
--db-path defaults) instead, which must never live inside the installed
package itself.
"""

from importlib.resources import files
from pathlib import Path


def default_data_dir() -> Path:
    """Path to the bundled default RSS feed configs (data/rss_feeds/*.json)."""
    return Path(str(files("strategic_reports.daily").joinpath("data", "rss_feeds")))
