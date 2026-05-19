"""End-to-end сценарий локального live-вьюера canvas/canvas.html.

Артист регистрируется на main-канвасе уникальным именем, открывается вьюер
в браузере, шлётся контрастный штрих через второй WS-сокет, проверяется что
на <canvas> в браузере появились не-белые пиксели в ожидаемой точке.

Powered by playwright-python (тот же сценарий, что в PRD описан под Playwright
MCP — здесь его можно прогнать `just test` или `pytest`).

Перед первым запуском: `uv run playwright install chromium`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets

from tests.server.test_canvas import line, new_artist_name, wait_for

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def http_server() -> Any:
    """Локальный HTTP-сервер для подачи canvas/canvas.html по http://, а не
    file:// — так WebSocket вызовы вьюера ходят из page-origin, без особых
    оговорок file://.
    """
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(REPO_ROOT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    # Поллим до готовности (не более 3с).
    for _ in range(30):
        try:
            with httpx.Client(timeout=0.2) as c:
                c.get(f"{base}/canvas/canvas.html").raise_for_status()
            break
        except httpx.HTTPError:
            import time

            time.sleep(0.1)
    else:
        proc.kill()
        proc.wait()
        pytest.fail("local http.server did not start")
    yield base
    proc.terminate()
    with contextlib.suppress(Exception):
        proc.wait(timeout=3)


@pytest.mark.asyncio
async def test_viewer_renders_incoming_stroke(
    http: httpx.Client, http_server: str
) -> None:
    """Вьюер должен отрисовать штрих, пришедший по WS на main-канвас."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright is not installed")

    artist_name = new_artist_name()
    r = http.post("/canvas/register", json={"artist_name": artist_name})
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"

    cfg = http.get("/canvas/config").json()
    width, height = int(cfg["width"]), int(cfg["height"])
    # Диагональный штрих через центр — точка (width/2, height/2) гарантированно
    # лежит на линии.
    stroke = line(
        100, 100, width - 100, height - 100, color="#ff0000", w=max(20, height // 24)
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(f"{http_server}/canvas/canvas.html")

            # Ждём, пока WS откроется и придёт `open` (canvas.width != 0).
            await page.wait_for_function(
                "() => window.__viewer && window.__viewer.ws"
                " && window.__viewer.ws.readyState === 1"
                " && window.__viewer.canvas.width > 0",
                timeout=10_000,
            )
            canvas_dim = await page.evaluate(
                "() => [window.__viewer.canvas.width, window.__viewer.canvas.height]"
            )
            assert canvas_dim == [width, height]

            # Замораживаем clearRect, чтобы пиксели накапливались и тайминг
            # 30fps не мешал чтению.
            await page.evaluate(
                """() => {
                    const ctx = window.__viewer.canvas.getContext('2d');
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
                    ctx.clearRect = () => {};
                }"""
            )

            # Шлём штрих с broadcaster-сокета (сервер не шлёт delta обратно
            # отправителю — нам нужен отдельный сокет).
            ws_url = "ws://195.133.25.57/canvas/ws"
            async with websockets.connect(ws_url) as broadcaster:
                await wait_for(broadcaster, "open")
                await wait_for(broadcaster, "snapshot")
                for _ in range(8):
                    await broadcaster.send(
                        json.dumps({"artist_name": artist_name, "segments": [stroke]})
                    )
                    await asyncio.sleep(0.1)

            # Считываем пиксель в центре линии.
            cx, cy = width // 2, height // 2
            pixel = await page.evaluate(
                f"""() => {{
                    const ctx = window.__viewer.canvas.getContext('2d');
                    const d = ctx.getImageData({cx}, {cy}, 1, 1).data;
                    return [d[0], d[1], d[2], d[3]];
                }}"""
            )
            r_, g_, b_, a_ = pixel
            assert a_ == 255, f"alpha at centre should be opaque, got {pixel}"
            assert r_ > 200 and g_ < 60 and b_ < 60, (
                f"expected red pixel at canvas centre, got {pixel}"
            )
        finally:
            await browser.close()
