# CLAUDE.md

## Архитектура

- `palette/` — палитры (наборы цветов).
- `brushes/` — Python-кисти; берут палитру и возвращают штрихи с цветом.
- `painting/` — картины: композиция из кистей.
- `canvas/canvas.html` — standalone HTML-вьюер, слушающий сервер по WS.

## Комманды

```bash
uv sync
just install-hooks
just audit                       # ruff + pyright + tach
just fix                         # auto-fix ruff
just clear                       # очистить общий холст на сервере
```

## Контракт с сервером

`.env`: `SERVER_URL`, `SERVER_WS_URL` (например `ws://195.133.25.57/canvas/ws`), `ARTIST_NAME` (только латиница).

На сервере **два изолированных канваса**: основной (`/canvas/*`) и тестовый (`/canvas/*-test`). Контракт у обоих одинаковый — отличается только path. Тестовый используется при прогоне smoke-тестов клиента, чтобы не загрязнять основной холст.

| Endpoint / сообщение | Main | Test |
|---|---|---|
| Размеры холста | `GET /canvas/config` | (общий) |
| Регистрация | `POST /canvas/register` `{ artist_name }` | `POST /canvas/register-test` |
| Очистка | `POST /canvas/clear` `{ secret }` | `POST /canvas/clear-test` |
| WS | `/canvas/ws` | `/canvas/ws-test` |
| Вьюер | `/canvas/view` | `/canvas/view-test` |

`POST /canvas/register*` → 200 / 409 / 422. `Stroke`: `{ x1, y1, x2, y2, color, w }`. В snapshot/delta каждый stroke дополнительно несёт `artist_name`.

WS-протокол: артист шлёт `{ artist_name, segments }`. Сервер: `open` → `snapshot` → `delta` / `clear`. Неизвестный `artist_name` → `{type:'error', error:'unknown artist_name'}`.

В тестах клиента используйте `register-test` + `ws-test` + `view-test` — изоляция от основного канваса гарантирована (отдельные `Set` артистов, отдельный пул strokes, отдельные pub/sub-темы).

Вьюер показывает выпадающее меню справа сверху для переключения слоёв по `artist_name`.