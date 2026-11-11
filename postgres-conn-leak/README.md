# Инцидент «Сайт то работает, то нет»

## Ситуация

Сервис заказов `orders-api` хранит данные в PostgreSQL. Пользователи жалуются, что сервис периодически перестаёт отвечать: запросы возвращают HTTP 503.

```bash
curl --silent http://127.0.0.1:8080/healthz
```

Дежурный уже дважды перезапускал PostgreSQL. Каждый раз всё работало, но через пару минут ошибки возвращались.

## Топология

```text
клиент → orders-api 127.0.0.1:8080 → PostgreSQL 16 (Unix-сокет, база incidentsim)
```

- `incidentsim-orders-api.service` — HTTP-сервис: `GET /healthz`, `POST /orders`, `GET /orders/<id>`. Каждый запрос открывает к базе собственное короткое соединение.
- `postgresql@16-main.service` — кластер PostgreSQL.

## Задача

Найдите причину, по которой приложение теряет доступ к базе, и устраните её так, чтобы ошибки не возвращались.

Ограничения:

- контракт `orders-api` менять нельзя;
- `max_connections` не должен превышать 200.

## Критерий готовности

```bash
bin/incidentctl verify postgres-conn-leak
```

Проверка в течение двух минут каждые 10 секунд обращается к `orders-api` и считает клиентские сессии в PostgreSQL. Все запросы должны выполняться успешно, а число сессий — оставаться стабильным. Перезапуск, после которого проблема возвращается, не засчитывается.

## Управление сценарием

```bash
bin/incidentctl infra postgres-conn-leak
bin/incidentctl incident postgres-conn-leak
bin/incidentctl verify postgres-conn-leak
```

После `incident` проверка должна падать, пока проблема не устранена.
