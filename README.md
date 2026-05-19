# artist

Стартовый репозиторий воркшопа. Пишем Python-кисти, палитры и работы — рисуем на общий канвас.

## Сервер

- API: <http://195.133.25.57>
- OpenAPI: <http://195.133.25.57/openapi>
- Общий канвас (вьюер): <http://195.133.25.57/canvas/view>
- Тестовый канвас (вьюер): <http://195.133.25.57/canvas/view-test>

## Команды

```bash
uv sync               # установить зависимости
just install-hooks    # pre-commit hooks
just audit            # ruff + pyright + tach
just fix              # auto-fix
just test             # прогнать интеграционный тест против сервера
```

Архитектура и контракт сервера — в [CLAUDE.md](CLAUDE.md).
