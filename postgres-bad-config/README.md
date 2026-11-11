# Инцидент «После релиза база не встала»

## Ситуация

Сервис заказов `orders-api` хранит данные в PostgreSQL. Ночью выкатили релиз с новыми параметрами производительности базы и перезапустили PostgreSQL. После этого база не поднялась, а приложение отвечает HTTP 503:

```bash
curl --silent http://127.0.0.1:8080/healthz
```

## Топология

```text
клиент → orders-api 127.0.0.1:8080 → PostgreSQL 16 (Unix-сокет, база incidentsim)
```

- `incidentsim-orders-api.service` — HTTP-сервис: `GET /healthz`, `POST /orders`, `GET /orders/<id>`.
- `postgresql@16-main.service` — кластер PostgreSQL. Конфигурация кластера лежит в `/etc/postgresql/16/main/`.
- Виртуальная машина: 1 vCPU, 1 GiB памяти.

## Задача

Верните базу в работу. Параметры производительности из релиза, которые подходят этому серверу, должны продолжать действовать: релиз откатывать нельзя.

## Критерий готовности

```bash
bin/incidentctl verify postgres-bad-config
```

Проверка убеждается, что PostgreSQL принимает подключения, `orders-api` сохраняет и читает заказ, параметры производительности из релиза действуют, а размер общего буфера PostgreSQL не превышает половины памяти ВМ.

## Управление сценарием

```bash
bin/incidentctl infra postgres-bad-config
bin/incidentctl incident postgres-bad-config
bin/incidentctl verify postgres-bad-config
```

После `incident` проверка должна падать, пока проблема не устранена.
