# Решение инцидента «Заказы не оформляются»

## Краткий ответ

На сервере работает `incidentsim-nightly-fix.service` — «ночная» правка данных под ролью `nightly_fix`. Она открывает транзакцию, берёт `LOCK TABLE orders IN EXCLUSIVE MODE`, выполняет `UPDATE` и ждёт проверки данных, которую так и не реализовали, — `COMMIT` не наступает никогда. `EXCLUSIVE` не мешает чтению, но блокирует любую запись, поэтому `GET` работает, а `POST /orders` висит.

Если сессию воркера оборвать (`pg_terminate_backend` или перезапуск PostgreSQL), он через 5 секунд переподключается и снова берёт блокировку.

Исправление — завершить блокирующую сессию и остановить сервис (`systemctl disable --now` закрывает его соединение вместе с процессом). Допустимо также задать `idle_in_transaction_session_timeout`: воркер проверяет эту политику и при заданном таймауте коммитит правку сразу, не удерживая транзакцию.

## Воспроизведение

```bash
bin/incidentctl incident postgres-lock
bin/incidentctl verify postgres-lock
```

```text
Sample 0: expected POST /orders to return HTTP 201 within 1 s, got -1:
```

Статус `-1` означает, что за секунду ответа не было: запрос не падает, а ждёт.

## Диагностика

1. Что делают сессии базы:

   ```bash
   runuser -u postgres -- psql incidentsim -c "
     SELECT pid, usename, application_name, state, wait_event_type,
            pg_blocking_pids(pid) AS blocked_by,
            now() - xact_start AS xact_age, left(query, 50) AS query
     FROM pg_stat_activity
     WHERE datname = 'incidentsim' AND pid <> pg_backend_pid()"
   ```

   ```text
    pid  |   usename   | application_name |        state        | wait_event_type | blocked_by | query
   ------+-------------+------------------+---------------------+-----------------+------------+------------------------------
    7256 | nightly_fix | nightly-fix      | idle in transaction | Client          | {}         | UPDATE orders SET note = ...
    7295 | orders_api  |                  | active              | Lock            | {7256}     | INSERT INTO orders ...
   ```

   `INSERT` приложения ждёт блокировку (`wait_event_type = Lock`), и `pg_blocking_pids` указывает на сессию `nightly-fix` в состоянии `idle in transaction`.

2. Какая блокировка держится:

   ```bash
   runuser -u postgres -- psql incidentsim -c "
     SELECT l.pid, l.mode, l.granted, a.application_name
     FROM pg_locks l JOIN pg_stat_activity a USING (pid)
     WHERE l.relation = 'orders'::regclass"
   ```

   `ExclusiveLock` у `nightly-fix` выдан (`granted = t`), `RowExclusiveLock` у приложения — нет.

3. Ожидания видны и в журнале (`log_lock_waits = on`):

   ```bash
   grep 'still waiting for' /var/log/postgresql/postgresql-16-main.log | tail -3
   ```

4. Источник блокировки:

   ```bash
   systemctl status incidentsim-nightly-fix.service --no-pager
   journalctl -u incidentsim-nightly-fix.service -n 5 --no-pager
   cat /opt/incidentctl/postgres-lock/nightly-fix/nightly_fix.py
   ```

   В коде `LOCK TABLE ... IN EXCLUSIVE MODE` и ожидание без `COMMIT` (`TODO: commit after the data check`). Unit перезапускается при сбое (`Restart=on-failure`), а сам скрипт после обрыва соединения повторяет попытку.

Причина найдена, если показано, что запись ждёт блокировки, которую держит простаивающая транзакция `nightly-fix`, и найден сервис, который её снова берёт.

## Корневая причина

Задание правки данных держит транзакцию с исключительной блокировкой таблицы бесконечно долго и после обрыва повторяет попытку. Перезапуск базы или завершение одной сессии лишь даёт ему начать заново.

## Исправление вручную

Основной вариант — остановить источник и завершить его сессию:

```bash
systemctl disable --now incidentsim-nightly-fix.service
runuser -u postgres -- psql incidentsim -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'nightly_fix'"
```

`ROLLBACK` незавершённой транзакции отменит только несохранённую правку `nightly-fix`; заказы, ожидавшие блокировки, сохранятся сразу после её снятия.

Допустимый вариант — запретить долгие простаивающие транзакции на уровне сервера:

```bash
runuser -u postgres -- psql incidentsim \
  -c "ALTER SYSTEM SET idle_in_transaction_session_timeout = '5s'" \
  -c "SELECT pg_reload_conf()" \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'nightly_fix'"
```

Новая настройка действует только на сессии, которые начнут простаивать после `reload`, поэтому текущую сессию воркера всё равно нужно завершить. Воркер переподключится, увидит политику, закоммитит правку сразу и завершится; `systemctl status` покажет `inactive`.

## Неправильные исправления

| Действие | Что происходит | Проверка |
|---|---|---|
| Перезапустить PostgreSQL | Через 5 секунд воркер снова берёт блокировку | падает: запись висит |
| Завершить ожидающие сессии приложения | Блокировка остаётся у `nightly-fix` | падает |
| Завершить только сессию `nightly-fix` | Воркер переподключается и блокирует снова | падает |

## Восстановление через исходный код упражнения

```bash
bin/incidentctl infra postgres-lock
```

Infra-playbook останавливает и удаляет `nightly-fix` и его пользователя, удаляет роль `nightly_fix` вместе с её сессиями и сбрасывает настройки `ALTER SYSTEM`, `ALTER ROLE … SET` и `ALTER DATABASE … SET`.

## Проверка решения

```bash
bin/incidentctl verify postgres-lock
```

Проверка длится около двух минут: 13 раз с интервалом 10 секунд оформляет заказ через `POST /orders` с тайм-аутом запроса 1 секунда. Каждый заказ должен вернуть HTTP 201 в пределах этой секунды.

Ожидаемый результат — 13 строк вида:

```text
Sample 12: order 31 saved.
```

Измерено на ВМ Ubuntu 24.04.5, 1 vCPU, 1 GiB: после правильного исправления все 13 заказов сохраняются; при каждой из ловушек проверка падает на первом же замере.
