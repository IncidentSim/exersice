# Инцидент «Заказы не оформляются»

## Ситуация

Сервис заказов `orders-api` хранит данные в PostgreSQL. Просмотр заказов работает, проверка здоровья отвечает `{"status":"ok"}`, но оформление нового заказа зависает: `POST /orders` не возвращает ответ.

```bash
curl --silent --max-time 5 --request POST \
  --header 'Content-Type: application/json' \
  --data '{"item":"book","note":"gift"}' \
  http://127.0.0.1:8080/orders
```

Ошибок в ответах нет — запросы просто не завершаются.

## Топология

```text
клиент → orders-api 127.0.0.1:8080 → PostgreSQL 16 (Unix-сокет, база incidentsim)
```

- `incidentsim-orders-api.service` — HTTP-сервис: `GET /healthz`, `POST /orders`, `GET /orders/<id>`.
- `postgresql@16-main.service` — кластер PostgreSQL.

## Задача

Найдите, что мешает записи новых заказов, и устраните причину так, чтобы проблема не вернулась.

Ограничения:

- контракт `orders-api` менять нельзя;
- данные заказов терять нельзя.

## Критерий готовности

```bash
bin/incidentctl verify postgres-lock
```

Проверка в течение двух минут каждые 10 секунд оформляет тестовый заказ через `orders-api`. Каждый заказ должен сохраняться быстрее чем за 1 секунду. Разовое улучшение, после которого проблема возвращается, не засчитывается.

## Управление сценарием

```bash
bin/incidentctl infra postgres-lock
bin/incidentctl incident postgres-lock
bin/incidentctl verify postgres-lock
```

После `incident` проверка должна падать, пока проблема не устранена.
