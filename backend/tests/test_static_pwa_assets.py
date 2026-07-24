from pathlib import Path

import pytest
from fastapi.responses import FileResponse

from app import main


@pytest.mark.parametrize(
    "asset_name",
    [
        "registerSW.js",
        "favicon.svg",
        "apple-touch-icon.png",
        "pwa-192.png",
        "pwa-512.png",
    ],
)
def test_static_response_serves_generated_root_pwa_assets(
    tmp_path: Path,
    asset_name: str,
) -> None:
    asset = tmp_path / asset_name
    asset.write_bytes(b"asset")
    (tmp_path / "index.html").write_text("index", encoding="utf-8")

    response = main._static_or_spa_response(tmp_path, asset_name)

    assert isinstance(response, FileResponse)
    assert Path(response.path) == asset
    assert response.headers["cache-control"] == "no-cache"


def test_static_response_keeps_react_routes_on_spa_fallback(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("index", encoding="utf-8")

    response = main._static_or_spa_response(tmp_path, "watchlist")

    assert isinstance(response, FileResponse)
    assert Path(response.path) == index
    assert response.headers["cache-control"] == "no-store, must-revalidate"
