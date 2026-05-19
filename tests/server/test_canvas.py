"""End-to-end сценарий контракта сервера.

Артист регистрируется уникальным именем и рисует чёрный квадрат на белом
фоне через WebSocket. Тест считается пройденным, когда сервер ретранслирует
эти штрихи viewer'у с добавленным `artist_name`.

После прогона картинка остаётся на http://195.133.25.57/canvas/view-test —
выбери слой с именем, напечатанным в выводе.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
from typing import Any

import httpx
import pytest
import websockets
from websockets.asyncio.client import ClientConnection


def new_artist_name() -> str:
    """20 случайных букв: на shared-сервере коллизия с другими прогонами
    практически невозможна (52²⁰ ≈ 10³⁴)."""
    return "demo" + "".join(random.choices(string.ascii_letters, k=20))


async def wait_for(
    ws: ClientConnection, kind: str, timeout: float = 10.0
) -> dict[str, Any]:
    """Ждать сообщение указанного типа, остальные — пропускать."""
    async with asyncio.timeout(timeout):
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == kind:
                return msg  # type: ignore[no-any-return]


def line(
    x1: float, y1: float, x2: float, y2: float, *, color: str, w: float
) -> dict[str, Any]:
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "color": color, "w": w}


@pytest.mark.asyncio
async def test_register_and_paint_black_square_on_white(
    http: httpx.Client, server_ws_base: str
) -> None:
    """Регистрация артиста → рисование квадрата → проверка broadcast."""
    artist_name = new_artist_name()

    cfg = http.get("/canvas/config").json()
    width, height = int(cfg["width"]), int(cfg["height"])

    r = http.post("/canvas/register-test", json={"artist_name": artist_name})
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"

    cx, cy = width / 2, height / 2
    side = min(width, height) / 4
    left, right = cx - side / 2, cx + side / 2
    top, bottom = cy - side / 2, cy + side / 2

    segments = [
        line(0, cy, width, cy, color="white", w=height),  # фон
        line(left, top, right, top, color="black", w=6),  # верх
        line(right, top, right, bottom, color="black", w=6),  # право
        line(right, bottom, left, bottom, color="black", w=6),  # низ
        line(left, bottom, left, top, color="black", w=6),  # лево
    ]

    # Сервер не шлёт delta обратно тому же сокету, что её отправил — поэтому
    # нужен второй сокет в роли viewer.
    ws_url = f"{server_ws_base}/canvas/ws-test"
    async with (
        websockets.connect(ws_url) as viewer,
        websockets.connect(ws_url) as broadcaster,
    ):
        opened = await wait_for(viewer, "open")
        assert opened["canvas"] == {"width": width, "height": height}
        await wait_for(viewer, "snapshot")
        await wait_for(broadcaster, "open")
        await wait_for(broadcaster, "snapshot")

        await broadcaster.send(
            json.dumps({"artist_name": artist_name, "segments": segments})
        )

        while True:
            delta = await wait_for(viewer, "delta")
            mine = [s for s in delta["strokes"] if s.get("artist_name") == artist_name]
            if mine:
                assert mine == [{**s, "artist_name": artist_name} for s in segments]
                break

    print(
        f"\n  ✓ painted as {artist_name!r}\n  → http://195.133.25.57/canvas/view-test"
    )
