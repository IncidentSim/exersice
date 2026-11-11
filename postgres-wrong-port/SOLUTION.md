# Решение инцидента «Приложение не видит базу»

## Краткий ответ

В `/etc/postgresql/16/main/conf.d/50-incidentsim.conf` порт кластера изменён на `5433`, а приложение по контракту подключается к `127.0.0.1:5432`. Сервис PostgreSQL при этом `active`, а `runuser -u postgres -- psql` работает: обёртка `pg_wrapper` в Ubuntu сама берёт порт кластера из его конфигурации.

Исправление — вернуть `port = 5432` и перезапустить кластер.

## Воспроизведение

```bash
bin/incidentctl incident postgres-wrong-port
bin/incidentctl verify postgres-wrong-port
```

```text
Expected PostgreSQL to accept TCP connections on 127.0.0.1:5432,
pg_isready reported: 127.0.0.1:5432 - no response (rc=2)
```

## Диагностика

1. Приложение получает отказ на уровне TCP, а не от PostgreSQL:

   ```bash
   curl --silent http://127.0.0.1:8080/healthz
   ```

   ```text
   connection to server at "127.0.0.1", port 5432 failed: Connection refused
   ```

2. Сервис запущен, администратор входит:

   ```bash
   systemctl is-active postgresql@16-main
   runuser -u postgres -- psql -c 'SELECT 1'
   ```

3. На каком порту на самом деле слушает кластер:

   ```bash
   ss -ltnp | grep postgres
   pg_lsclusters
   ```

   `postgres` слушает `127.0.0.1:5433`, `pg_lsclusters` показывает `16 main 5433 online`.

4. Где задан порт:

   ```bash
   runuser -u postgres -- psql -c \
     "SELECT name, setting, sourcefile, sourceline FROM pg_settings WHERE name = 'port'"
   ```

   Значение `5433` берётся из `conf.d/50-incidentsim.conf`, который подключается после `postgresql.conf` и перекрывает его `port = 5432`.

Причина найдена, если показано, что кластер слушает не тот порт, на который рассчитан контракт, и найдена строка, которая это задаёт.

## Корневая причина

При обслуживании в конфигурацию кластера внесли `port = 5433`. Изменение вступило в силу после перезапуска, и клиенты, подключающиеся к `5432`, потеряли базу.

## Исправление вручную

```bash
sed -i 's/^port = 5433$/port = 5432/' /etc/postgresql/16/main/conf.d/50-incidentsim.conf
runuser -u postgres -- /usr/lib/postgresql/16/bin/postgres \
  --config-file=/etc/postgresql/16/main/postgresql.conf -C port
systemctl restart postgresql@16-main
pg_isready -h 127.0.0.1 -p 5432
```

`port` меняется только перезапуском: `reload` его не применит.

## Неправильные исправления

| Действие | Что происходит | Проверка |
|---|---|---|
| Перенастроить приложение на `5433` | `/healthz` снова отвечает `ok`, но база недоступна по контрактному адресу для остальных клиентов | падает: `127.0.0.1:5432` не отвечает |
| Перезапустить PostgreSQL | Кластер снова поднимается на `5433` | падает |

## Восстановление через исходный код упражнения

```bash
bin/incidentctl infra postgres-wrong-port
```

Infra-playbook возвращает `conf.d/50-incidentsim.conf` и `/etc/incidentsim/orders-api.env`, проверяет конфигурацию через `postgres -C` и перезапускает кластер и приложение.

## Проверка решения

```bash
bin/incidentctl verify postgres-wrong-port
```

Проверка:

- выполняет `pg_isready -h 127.0.0.1 -p 5432`;
- входит ролью `orders_api` в `incidentsim` по TCP `127.0.0.1:5432` с паролем из `/etc/incidentsim/orders-api.password`;
- сохраняет заказ через `POST /orders` и читает его через `GET /orders/<id>`.

Ожидаемый результат:

```text
PostgreSQL accepts TCP connections on 127.0.0.1:5432.
orders_api logs in over TCP with its password.
The application stores and reads orders.
```
