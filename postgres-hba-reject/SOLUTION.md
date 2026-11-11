# Решение инцидента «Пускают не всех»

## Краткий ответ

В начало `/etc/postgresql/16/main/pg_hba.conf` добавлено правило «на время работ»:

```text
host    all               all           127.0.0.1/32     reject
```

PostgreSQL применяет первое подходящее правило, поэтому любой TCP-вход с `127.0.0.1` отклоняется ещё до проверки пароля, а правило для `orders_api` ниже никогда не срабатывает. Администратор входит через Unix-сокет (`local … peer`), на который правило `host` не действует, поэтому база выглядит исправной.

Исправление — удалить правило `reject` (или поставить правило приложения выше него) и перечитать конфигурацию.

## Воспроизведение

```bash
bin/incidentctl incident postgres-hba-reject
bin/incidentctl verify postgres-hba-reject
```

```text
Expected orders_api to log in to incidentsim at 127.0.0.1:5432, got rc=2:
psql: error: connection to server at "127.0.0.1", port 5432 failed:
FATAL:  pg_hba.conf rejects connection for host "127.0.0.1", user "orders_api", database "incidentsim"
```

## Диагностика

1. Ответ приложения и журнал сервера называют причину:

   ```bash
   curl --silent http://127.0.0.1:8080/healthz
   grep 'rejects connection' /var/log/postgresql/postgresql-16-main.log | tail -3
   ```

   `pg_hba.conf rejects connection` означает отказ по правилу `reject`, а не ошибку пароля: в этом случае было бы `password authentication failed`.

2. Сервер работает и пускает администратора через сокет:

   ```bash
   sudo -u postgres psql -c 'SELECT 1'
   ```

3. Правила в том порядке, в каком их применяет сервер:

   ```bash
   sudo -u postgres psql -c \
     "SELECT rule_number, line_number, type, database, user_name, address, auth_method
      FROM pg_hba_file_rules ORDER BY rule_number"
   ```

   Правило номер 1 — `host all all 127.0.0.1/32 reject`; правило для `incidentsim`/`orders_api` идёт после него.

4. Воспроизвести вход приложения вручную:

   ```bash
   PGPASSWORD="$(cat /etc/incidentsim/orders-api.password)" \
     psql -h 127.0.0.1 -U orders_api -d incidentsim -c 'SELECT current_user'
   ```

Причина найдена, если показано, что отказ происходит по правилу `reject`, которое стоит выше правила приложения.

## Корневая причина

Временное правило для работ (`OPS-1432`) оставили в начале `pg_hba.conf`. Оно перекрывает все TCP-подключения с loopback, включая подключение приложения.

## Исправление вручную

Удалите блок между строками `# BEGIN maintenance window OPS-1432` и `# END maintenance window OPS-1432`, затем проверьте и примените правила:

```bash
sudo -u postgres psql -c 'SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL'
systemctl reload postgresql@16-main
```

Если правило блокировки действительно нужно для других клиентов, допустимо оставить его, но поставить правило `host incidentsim orders_api 127.0.0.1/32 scram-sha-256` выше.

Перезапуск PostgreSQL не нужен: `pg_hba.conf` применяется при `reload`.

## Неправильные исправления

| Действие | Что происходит | Проверка |
|---|---|---|
| Поставить `trust` для всех TCP-подключений | Приложение входит, но в базу пускают с любым паролем | падает: неверный пароль принят |
| Добавить сверху `host all all 127.0.0.1/32 trust` | То же самое | падает: неверный пароль принят |
| Перевести приложение на Unix-сокет | Нарушен контракт подключения приложения | падает: вход `orders_api` по TCP отклоняется |
| Перезапустить PostgreSQL | Правила не меняются | падает |

## Восстановление через исходный код упражнения

```bash
bin/incidentctl infra postgres-hba-reject
```

Infra-playbook записывает исправный `pg_hba.conf`, проверяет его через `pg_hba_file_rules` и выполняет `reload`.

## Проверка решения

```bash
bin/incidentctl verify postgres-hba-reject
```

Проверка:

- входит ролью `orders_api` в `incidentsim` по TCP `127.0.0.1:5432` с паролем из `/etc/incidentsim/orders-api.password` — вход должен пройти;
- входит той же ролью с неверным паролем — вход должен отклониться с `password authentication failed`;
- сохраняет заказ через `POST /orders` и читает его через `GET /orders/<id>`.

Ожидаемый результат:

```text
orders_api logs in over TCP with its password.
A wrong password is refused by password authentication.
The application stores and reads orders.
```

Пароль `orders_api` генерирует infra-playbook на самой ВМ при первом запуске; в упражнении он не хранится.
