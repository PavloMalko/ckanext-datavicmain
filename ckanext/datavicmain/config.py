from __future__ import annotations

import ckan.plugins.toolkit as tk

CONFIG_PAGES_BASE_URL = "ckan.pages.base_url"


def get_pages_base_url() -> str:
    return tk.config[CONFIG_PAGES_BASE_URL]
