"""Фикстуры для интеграционного теста сервера.

Тест работает с тестовым канвасом (`*-test`), чтобы не загрязнять основной.
Сервер — постоянный хост воркшопа.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

SERVER_URL = "http://195.133.25.57"
SERVER_WS_BASE = "ws://195.133.25.57"


@pytest.fixture(scope="session")
def server_url() -> str:
    return SERVER_URL


@pytest.fixture(scope="session")
def server_ws_base() -> str:
    return SERVER_WS_BASE


@pytest.fixture(scope="session", autouse=True)
def _require_server(server_url: str) -> None:
    """Сервер обязан отвечать на /health, иначе тестам делать нечего."""
    try:
        httpx.get(f"{server_url}/health", timeout=3.0).raise_for_status()
    except (httpx.HTTPError, OSError) as e:
        pytest.exit(
            f"\n  Server unreachable at {server_url}: {e}\n",
            returncode=2,
        )


@pytest.fixture
def http(server_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server_url, timeout=5.0) as client:
        yield client
