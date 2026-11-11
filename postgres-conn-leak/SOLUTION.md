# Решение инцидента «Сайт то работает, то нет»

## Краткий ответ

На сервере работает `incidentsim-report-worker.service` — воркер отчётов под ролью `report_worker`. Каждые 4 секунды он открывает новое соединение, строит отчёт и «сохраняет сессию для следующего запуска», но больше её не использует и не закрывает. При `max_connections = 25` (из них 3 зарезервированы для суперпользователя) примерно за полторы минуты воркер занимает все 22 обычных слота, и `orders-api` больше не может подключиться.

Перезапуск PostgreSQL разрывает все сессии воркера, поэтому на пару минут всё начинает работать. Затем воркер снова занимает слоты.

Исправление — остановить источник утечки: остановить и отключить воркер (его сессии закроются вместе с процессом). Допустимо и ограничить утечку средствами PostgreSQL: `CONNECTION LIMIT` для роли `report_worker` или `idle_session_timeout`.

## Воспроизведение

```bash
bin/incidentctl incident postgres-conn-leak
bin/incidentctl verify postgres-conn-leak
```

```text
Expected HTTP 201 from POST /orders, got 503:
{"error": "database unavailable", "detail": "connection to server on socket
\"/var/run/postgresql/.s.PGSQL.5432\" failed: FATAL:  remaining connection slots
are reserved for roles with the SUPERUSER attribute"}
```

В PostgreSQL 16 обычная роль получает именно это сообщение. `sorry, too many clients already` появляется, когда заняты и зарезервированные слоты.

## Диагностика

1. Ошибка — нехватка соединений, а не недоступность сервера:

   ```bash
   curl --silent http://127.0.0.1:8080/healthz
   grep -c 'connection slots are reserved' /var/log/postgresql/postgresql-16-main.log
   ```

2. Суперпользователь подключается через зарезервированный слот. Кто держит сессии:

   ```bash
   sudo -u postgres psql -c "
     SELECT usename, application_name, state, count(*),
            min(backend_start) AS oldest, max(state_change) AS last_activity
     FROM pg_stat_activity
     WHERE backend_type = 'client backend'
     GROUP BY 1, 2, 3"
   ```

   ```text
    usename       | application_name | state | count
   ---------------+------------------+-------+------
    report_worker | report-worker    | idle  |    22
   ```

   Все слоты заняты простаивающими (`idle`) сессиями одной роли.

3. После перезапуска PostgreSQL число сессий `report_worker` растёт на одну каждые ~4 секунды:

   ```bash
   watch -n 5 "sudo -u postgres psql -Atc \"SELECT count(*) FROM pg_stat_activity WHERE usename = 'report_worker'\""
   ```

4. Источник:

   ```bash
   ps -u report_worker -o pid,cmd
   systemctl status incidentsim-report-worker.service --no-pager
   journalctl -u incidentsim-report-worker.service -n 5 --no-pager
   cat /opt/incidentctl/postgres-conn-leak/report-worker/worker.py
   ```

   В журнале видно `sessions=1, 2, 3 …`, в коде — `sessions.append(conn)` без закрытия.

Причина найдена, если показано, что слоты занимает одна роль, её сессии накапливаются со временем и найден процесс, который их открывает.

## Корневая причина

Ошибка в `report-worker`: соединение после каждого отчёта сохраняется в список и никогда не закрывается. Маленький `max_connections` лишь делает отказ быстрым — при любом лимите утечка его исчерпает.

## Исправление вручную

Основной вариант — остановить воркер:

```bash
systemctl disable --now incidentsim-report-worker.service
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity WHERE usename = 'report_worker'"
```

Сессии закрываются вместе с процессом. Если воркер нужен, его код надо исправить (закрывать соединение после отчёта) до повторного запуска.

Допустимые варианты, если остановить воркер нельзя:

```bash
# не больше 5 сессий для роли воркера
sudo -u postgres psql \
  -c "ALTER ROLE report_worker CONNECTION LIMIT 5" \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'report_worker'"

# или закрывать простаивающие сессии через 30 секунд
sudo -u postgres psql \
  -c "ALTER SYSTEM SET idle_session_timeout = '30s'" \
  -c "SELECT pg_reload_conf()" \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'report_worker'"
```

Без завершения уже открытых сессий эти настройки начнут действовать только для новых подключений.

## Неправильные исправления

| Действие | Что происходит | Проверка |
|---|---|---|
| Только перезапустить PostgreSQL | Через ~90 секунд слоты снова заняты | падает: приложение получает 503 |
| Завершить сессии воркера, не останавливая его | Воркер открывает новые | падает: приложение получает 503 |
| `max_connections = 1000` | Отказ лишь откладывается, памяти на такой лимит нет | падает: лимит выше 200 |
| `max_connections = 200` | Отказ откладывается на ~12 минут | падает: число сессий растёт (1 → 32 за окно) |

## Восстановление через исходный код упражнения

```bash
bin/incidentctl infra postgres-conn-leak
```

Infra-playbook останавливает и удаляет воркер и его пользователя, удаляет роль `report_worker` вместе с её сессиями, сбрасывает настройки `ALTER SYSTEM`, `ALTER ROLE … SET` и `ALTER DATABASE … SET` и возвращает `max_connections = 25`.

## Проверка решения

```bash
bin/incidentctl verify postgres-conn-leak
```

Проверка длится около двух минут:

- `max_connections` должен быть не больше 200;
- сохраняет тестовый заказ, затем 13 раз с интервалом 10 секунд обращается к `GET /healthz` и `GET /orders/<id>` — все ответы должны быть HTTP 200;
- на каждом замере считает клиентские сессии в `pg_stat_activity`; во второй половине окна их число должно меняться не больше чем на 3.

Вторая половина окна нужна, чтобы допустимые исправления успели выйти на стабильный уровень: при `CONNECTION LIMIT 5` воркер сначала набирает 5 сессий, при `idle_session_timeout = 30s` — около 7–8.

Измеренные результаты на ВМ Ubuntu 24.04.5, 1 vCPU, 1 GiB:

| Исправление | Сессии во второй половине окна |
|---|---|
| Остановить воркер | 0 |
| `CONNECTION LIMIT 5` | 5 |
| `idle_session_timeout = 30s` | 7–8 |
| Без исправления, `max_connections = 200` | 1 → 32, растут |
