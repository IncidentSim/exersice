# Инцидент «Пускают не всех»

## Ситуация

Сервис заказов `orders-api` перестал работать с базой: `GET /healthz` отвечает HTTP 503.

```bash
curl --silent http://127.0.0.1:8080/healthz
```

При этом PostgreSQL запущен, и администратор без проблем заходит в базу:

```bash
runuser -u postgres -- psql incidentsim
```

## Топология

```text
клиент → orders-api 127.0.0.1:8080 → PostgreSQL 16 127.0.0.1:5432 (TCP, пароль)
```

- `incidentsim-orders-api.service` — HTTP-сервис: `GET /healthz`, `POST /orders`, `GET /orders/<id>`.
- Приложение подключается к базе `incidentsim` ролью `orders_api` по TCP с паролем. Пароль хранится в `/etc/incidentsim/orders-api.password`, настройки подключения — в `/etc/incidentsim/orders-api.env`.
- `postgresql@16-main.service` — кластер PostgreSQL, конфигурация в `/etc/postgresql/16/main/`.

## Задача

Найдите, почему приложение не может войти в базу, и восстановите доступ.

Ограничения:

- приложение по-прежнему входит по TCP с паролем; настройки подключения приложения менять нельзя;
- вход с неверным паролем должен отклоняться.

## Критерий готовности

```bash
bin/incidentctl verify postgres-hba-reject
```

Проверка входит в базу ролью `orders_api` по TCP с правильным паролем и с неверным: первый вход должен пройти, второй — отклониться проверкой пароля. Затем она сохраняет и читает заказ через `orders-api`.

## Управление сценарием

```bash
bin/incidentctl infra postgres-hba-reject
bin/incidentctl incident postgres-hba-reject
bin/incidentctl verify postgres-hba-reject
```

После `incident` проверка должна падать, пока проблема не устранена.
