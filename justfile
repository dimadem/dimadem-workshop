set dotenv-load := true

audit:
    @echo "\033[35mlinter\033[0m"
    @uv run ruff check brushes/ palette/ painting/ tests/
    @echo "\033[35mformatter\033[0m"
    @uv run ruff format brushes/ palette/ painting/ tests/ --check
    @echo "\033[35mtypes\033[0m"
    @uv run pyright brushes palette painting
    @echo "\033[35marchitecture\033[0m"
    @uv run tach check

fix:
    @uv run ruff check brushes/ palette/ painting/ tests/ --fix
    @uv run ruff format brushes/ palette/ painting/ tests/

install-hooks:
    @uv run pre-commit install

test:
    @curl -sfm 3 http://195.133.25.57/health >/dev/null || { \
      echo "\033[31m✗ server unreachable at http://195.133.25.57\033[0m"; \
      exit 1; \
    }
    @uv run pytest tests/ -v -s -p no:tach
