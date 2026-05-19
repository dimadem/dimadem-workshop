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


@pytest.mark.asyncio
async def test_viewer_artist_filter_dropdown(
    http: httpx.Client, http_server: str
) -> None:
    """Dropdown должен фильтровать штрихи по artist_name.

    Сценарий: регистрируем двух уникальных артистов, шлём контрастные штрихи
    от обоих по разным половинам канваса, открываем dropdown, снимаем галочку
    у одного, шлём новую серию — проверяем, что только включённый артист
    отрисован, выключенный — белый фон.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright is not installed")

    artist_a = new_artist_name()
    artist_b = new_artist_name()
    for name in (artist_a, artist_b):
        r = http.post("/canvas/register", json={"artist_name": name})
        assert r.status_code == 200, f"register {name} failed: {r.status_code} {r.text}"

    cfg = http.get("/canvas/config").json()
    width, height = int(cfg["width"]), int(cfg["height"])
    # Толстая линия по половинам, чтобы пиксель в центре каждой половины
    # гарантированно попал на штрих.
    thickness = max(40, height // 6)
    quarter_w = width // 4
    y_mid = height // 2
    stroke_a = line(50, y_mid, quarter_w * 2 - 50, y_mid, color="#ff0000", w=thickness)
    stroke_b = line(
        quarter_w * 2 + 50, y_mid, width - 50, y_mid, color="#0000ff", w=thickness
    )

    async def broadcast(strokes_by_artist: dict[str, list[dict[str, Any]]]) -> None:
        ws_url = "ws://195.133.25.57/canvas/ws"
        async with websockets.connect(ws_url) as bc:
            await wait_for(bc, "open")
            await wait_for(bc, "snapshot")
            for _ in range(8):
                for name, segments in strokes_by_artist.items():
                    await bc.send(
                        json.dumps({"artist_name": name, "segments": segments})
                    )
                await asyncio.sleep(0.1)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(f"{http_server}/canvas/canvas.html")

            await page.wait_for_function(
                "() => window.__viewer && window.__viewer.ws"
                " && window.__viewer.ws.readyState === 1"
                " && window.__viewer.canvas.width > 0",
                timeout=10_000,
            )

            # Замораживаем clearRect и заливаем фон белым — пиксели накапливаются.
            await page.evaluate(
                """() => {
                    const ctx = window.__viewer.canvas.getContext('2d');
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
                    ctx.clearRect = () => {};
                }"""
            )

            # 1) Шлём оба артиста — оба должны попасть в dropdown с enabled=true.
            await broadcast({artist_a: [stroke_a], artist_b: [stroke_b]})
            await page.wait_for_function(
                "() => { const e = window.__viewer.artistFilter.entries();"
                "  const names = e.map(x => x[0]);"
                f"  return names.includes({artist_a!r})"
                f"    && names.includes({artist_b!r}); }}",
                timeout=10_000,
            )
            entries = await page.evaluate(
                "() => window.__viewer.artistFilter.entries()"
            )
            entries_map = dict(entries)
            assert entries_map.get(artist_a) is True
            assert entries_map.get(artist_b) is True

            # 2) Открываем dropdown, проверяем что чекбоксы появились.
            await page.click("#filter-toggle")
            await page.wait_for_selector(
                f"#filter-list input[data-artist={artist_a!r}]", state="visible"
            )
            await page.wait_for_selector(
                f"#filter-list input[data-artist={artist_b!r}]", state="visible"
            )

            # 3) Снимаем галочку у artist_b и убеждаемся, что состояние ушло
            # в artistFilter.
            await page.click(f"#filter-list input[data-artist={artist_b!r}]")
            await page.wait_for_function(
                f"() => window.__viewer.artistFilter.isEnabled({artist_a!r}) === true"
                f" && window.__viewer.artistFilter.isEnabled({artist_b!r}) === false",
                timeout=5_000,
            )

            # 4) Перед новым раундом — сбрасываем накопленные пиксели.
            await page.evaluate(
                """() => {
                    const ctx = window.__viewer.canvas.getContext('2d');
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
                }"""
            )

            # 5) Шлём новую серию от обоих — выключенный (artist_b) рисоваться
            # не должен; включённый (artist_a) рисуется.
            await broadcast({artist_a: [stroke_a], artist_b: [stroke_b]})

            # Подождём пару кадров, чтобы накопить штрихи.
            await asyncio.sleep(0.5)

            px_a = await page.evaluate(
                f"""() => {{
                    const ctx = window.__viewer.canvas.getContext('2d');
                    const d = ctx.getImageData({quarter_w}, {y_mid}, 1, 1).data;
                    return [d[0], d[1], d[2], d[3]];
                }}"""
            )
            px_b = await page.evaluate(
                f"""() => {{
                    const ctx = window.__viewer.canvas.getContext('2d');
                    const d = ctx.getImageData({quarter_w * 3}, {y_mid}, 1, 1).data;
                    return [d[0], d[1], d[2], d[3]];
                }}"""
            )
            r_a, g_a, b_a, _ = px_a
            r_b, g_b, b_b, _ = px_b
            assert r_a > 200 and g_a < 80 and b_a < 80, (
                f"expected enabled artist_a (red) at left half, got {px_a}"
            )
            assert r_b > 240 and g_b > 240 and b_b > 240, (
                f"expected disabled artist_b → white at right half, got {px_b}"
            )

            # 6) Возвращаем галочку — следующая серия снова рисует artist_b.
            await page.click(f"#filter-list input[data-artist={artist_b!r}]")
            await page.wait_for_function(
                f"() => window.__viewer.artistFilter.isEnabled({artist_b!r}) === true",
                timeout=5_000,
            )
            await page.evaluate(
                """() => {
                    const ctx = window.__viewer.canvas.getContext('2d');
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
                }"""
            )
            await broadcast({artist_b: [stroke_b]})
            await asyncio.sleep(0.5)
            px_b2 = await page.evaluate(
                f"""() => {{
                    const ctx = window.__viewer.canvas.getContext('2d');
                    const d = ctx.getImageData({quarter_w * 3}, {y_mid}, 1, 1).data;
                    return [d[0], d[1], d[2], d[3]];
                }}"""
            )
            r_b2, g_b2, b_b2, _ = px_b2
            assert b_b2 > 200 and r_b2 < 80 and g_b2 < 80, (
                f"expected re-enabled artist_b (blue) at right half, got {px_b2}"
            )

            # 7) WS-соединение осталось живым.
            ws_state = await page.evaluate("() => window.__viewer.ws.readyState")
            assert ws_state == 1, f"WS expected OPEN (1), got {ws_state}"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_viewer_handle_clear_resets_state(
    http: httpx.Client, http_server: str
) -> None:
    """`clear` сбрасывает буфер кадра, artistFilter и dropdown.

    Сценарий: уникальный артист шлёт контрастный штрих, артист появляется в
    dropdown, на холсте видны цветные пиксели. Затем эмулируется приход
    `{type: 'clear'}` по WS (POST /canvas/clear требует CLEAR_SECRET, недоступного
    локально; ws.dispatchEvent проверяет ту же ветку обработчика). После clear
    dropdown пуст, следующий кадр — белый. Новые штрихи снова заполняют
    dropdown с enabled=true, WS остаётся OPEN.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright is not installed")

    artist_name = new_artist_name()
    r = http.post("/canvas/register", json={"artist_name": artist_name})
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"

    cfg = http.get("/canvas/config").json()
    width, height = int(cfg["width"]), int(cfg["height"])
    stroke = line(
        100, 100, width - 100, height - 100, color="#ff0000", w=max(20, height // 24)
    )

    async def broadcast_once() -> None:
        ws_url = "ws://195.133.25.57/canvas/ws"
        async with websockets.connect(ws_url) as bc:
            await wait_for(bc, "open")
            await wait_for(bc, "snapshot")
            for _ in range(8):
                await bc.send(
                    json.dumps({"artist_name": artist_name, "segments": [stroke]})
                )
                await asyncio.sleep(0.1)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(f"{http_server}/canvas/canvas.html")

            await page.wait_for_function(
                "() => window.__viewer && window.__viewer.ws"
                " && window.__viewer.ws.readyState === 1"
                " && window.__viewer.canvas.width > 0",
                timeout=10_000,
            )

            # Замораживаем clearRect и заливаем фон белым, чтобы пиксели
            # накапливались между кадрами — детект цветного штриха становится
            # надёжным.
            await page.evaluate(
                """() => {
                    const ctx = window.__viewer.canvas.getContext('2d');
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
                    ctx.clearRect = () => {};
                }"""
            )

            # 1) Шлём штрих → артист попадает в dropdown, на холсте красный.
            await broadcast_once()
            await page.wait_for_function(
                "() => window.__viewer.artistFilter.entries()"
                f"  .some(e => e[0] === {artist_name!r})",
                timeout=10_000,
            )
            cx, cy = width // 2, height // 2
            px_before = await page.evaluate(
                f"""() => {{
                    const ctx = window.__viewer.canvas.getContext('2d');
                    const d = ctx.getImageData({cx}, {cy}, 1, 1).data;
                    return [d[0], d[1], d[2], d[3]];
                }}"""
            )
            r0, g0, b0, _ = px_before
            assert r0 > 200 and g0 < 80 and b0 < 80, (
                f"expected red stroke at canvas centre before clear, got {px_before}"
            )

            # 2) Эмулируем приход clear от сервера. Восстанавливаем clearRect,
            # чтобы render-loop снова мог стереть кадр после clear.
            await page.evaluate(
                """() => {
                    const ctx = window.__viewer.canvas.getContext('2d');
                    delete ctx.clearRect;
                    window.__viewer.ws.dispatchEvent(new MessageEvent('message', {
                        data: JSON.stringify({ type: 'clear' }),
                    }));
                }"""
            )

            # 3) artistFilter и dropdown очищены.
            await page.wait_for_function(
                "() => window.__viewer.artistFilter.entries().length === 0",
                timeout=5_000,
            )
            # dropdown открыт явно, чтобы прочитать его содержимое — кнопка
            # остаётся на месте.
            await page.click("#filter-toggle")
            await page.wait_for_selector("#filter-list .empty", state="visible")
            checkbox_count = await page.evaluate(
                "() => document.querySelectorAll("
                "  '#filter-list input[type=\"checkbox\"]'"
                ").length"
            )
            assert checkbox_count == 0, (
                f"expected dropdown to be empty after clear, got {checkbox_count} items"
            )

            # 4) Следующий кадр — белый (нет новых штрихов). Ждём минимум
            # 2 кадра 30fps, плюс запас на latency.
            await asyncio.sleep(0.2)
            px_after_clear = await page.evaluate(
                f"""() => {{
                    const ctx = window.__viewer.canvas.getContext('2d');
                    const d = ctx.getImageData({cx}, {cy}, 1, 1).data;
                    return [d[0], d[1], d[2], d[3]];
                }}"""
            )
            r1, g1, b1, a1 = px_after_clear
            # После реального clearRect (без фоновой заливки) пиксель —
            # прозрачный → CSS-фон #ffffff даёт белый через прозрачный alpha.
            # Проверяем оба варианта: либо непрозрачный белый, либо прозрачный.
            is_white_opaque = r1 > 240 and g1 > 240 and b1 > 240 and a1 == 255
            is_transparent = a1 == 0
            assert is_white_opaque or is_transparent, (
                f"expected white/transparent canvas after clear, got {px_after_clear}"
            )

            # 5) WS-соединение по-прежнему открыто.
            ws_state = await page.evaluate("() => window.__viewer.ws.readyState")
            assert ws_state == 1, f"WS expected OPEN (1) after clear, got {ws_state}"

            # 6) Новые штрихи после clear возвращают артиста в dropdown
            # с enabled=true.
            await broadcast_once()
            await page.wait_for_function(
                "() => { const e = window.__viewer.artistFilter.entries();"
                f"  const me = e.find(x => x[0] === {artist_name!r});"
                "  return me && me[1] === true; }",
                timeout=10_000,
            )
        finally:
            await browser.close()
