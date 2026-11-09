"""The static frontend: assets exist, config is valid JSON, scripts only reference files that are there."""

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def test_index_references_existing_assets():
    html = (WEB / "index.html").read_text()
    for ref in re.findall(r'(?:href|src)="([^"]+)"', html):
        assert (WEB / ref).exists(), ref


def test_config_json_is_valid_and_points_at_the_api_prefix():
    assert json.loads((WEB / "config.json").read_text())["apiBase"] == "/api"


def test_the_app_only_uses_relative_api_calls():
    js = (WEB / "app.js").read_text() + (WEB / "api.js").read_text()
    assert "http://localhost" not in js and "127.0.0.1" not in js
