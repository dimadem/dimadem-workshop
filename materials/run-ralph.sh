#!/usr/bin/env bash
# Запускает ralph-loop, который ходит по issue в materials/issues/.
set -euo pipefail
cd "$(dirname "$0")/.."

exec claude \
  --permission-mode bypassPermissions \
  "/ralph-loop:ralph-loop \"Возьми следующую невыполненную issue из materials/issues/. 
Создай отдельный branch, реализуй задачу: строго проходи по критериям, отметь \\\`[x]\\\` прямо в файле. 
После выполненной задачи сделай пуллреквест в main branch и переключись обратно в main. 
Для перехода к следующей задаче, проверь закрыт ли PR предыдущей задачи в gh. 
Если PR предыдущей задачи не закрыт, не выполняй следующую задачу.
Когда все issue в папке закрыты — выведи <promise>DONE</promise>. 
Не ври, не комить.\" --completion-promise 'DONE' --max-iterations 50"
