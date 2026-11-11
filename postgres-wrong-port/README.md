# Инцидент «Приложение не видит базу»

## Ситуация

После планового обслуживания сервис заказов `orders-api` перестал работать с базой: `GET /healthz` отвечает HTTP 503.

```bash
curl --silent http://127.0.0.1:8080/healthz
```

Дежурный проверил PostgreSQL: сервис `active`, администратор заходит в базу без ошибок.

## Топология

```text
клиент → orders-api 127.0.0.1:8080 → PostgreSQL 16 127.0.0.1:5432 (TCP, пароль)
```

- `incidentsim-orders-api.service` — HTTP-сервис: `GET /healthz`, `POST /orders`, `GET /orders/<id>`.
- Приложение подключается к базе `incidentsim` ролью `orders_api` по TCP к `127.0.0.1:5432`. Пароль хранится в `/etc/incidentsim/orders-api.password`, настройки подключения — в `/etc/incidentsim/orders-api.env`.
- `postgresql@16-main.service` — кластер PostgreSQL, конфигурация в `/etc/postgresql/16/main/`.

## Задача

Найдите, почему приложение не может подключиться к базе, и восстановите работу.

Ограничение: адрес базы `127.0.0.1:5432` — часть контракта, на него рассчитаны и другие клиенты. Настройки подключения приложения менять нельзя.

## Критерий готовности

```bash
bin/incidentctl verify postgres-wrong-port
```

Проверка убеждается, что PostgreSQL принимает TCP-подключения на `127.0.0.1:5432`, роль `orders_api` входит туда со своим паролем, а `orders-api` сохраняет и читает заказ.

## Управление сценарием

```bash
bin/incidentctl infra postgres-wrong-port
bin/incidentctl incident postgres-wrong-port
bin/incidentctl verify postgres-wrong-port
```

После `incident` проверка должна падать, пока проблема не устранена.
