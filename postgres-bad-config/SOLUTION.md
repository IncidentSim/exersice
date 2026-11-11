# Решение инцидента «После релиза база не встала»

## Краткий ответ

Релиз заменил файл тюнинга `/etc/postgresql/16/main/conf.d/60-tuning.conf`. В нём строка

```ini
shared_bufers = 8GB
```

содержит сразу две ошибки:

1. опечатку в имени параметра — PostgreSQL не знает `shared_bufers` и отказывается читать конфигурацию;
2. размер, рассчитанный на другой сервер — 8 GB общего буфера на ВМ с 1 GiB памяти не выделяются.

Исправление — привести строку к виду `shared_buffers = 256MB` (не больше половины памяти ВМ) и сохранить остальные параметры файла: `work_mem = 16MB`, `effective_cache_size = 512MB`, `maintenance_work_mem = 64MB`.

## Воспроизведение

```bash
bin/incidentctl incident postgres-bad-config
bin/incidentctl verify postgres-bad-config
```

```text
PostgreSQL does not accept local connections: psql: error: connection to server
on socket "/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory
```

`orders-api` при этом отвечает HTTP 503 `database unavailable`.

## Диагностика

1. Сервис не запущен:

   ```bash
   systemctl status postgresql@16-main --no-pager
   ```

   Статус `failed`, но причины здесь нет.

2. Причина в журнале PostgreSQL:

   ```bash
   tail -n 20 /var/log/postgresql/postgresql-16-main.log
   ```

   ```text
   LOG:  unrecognized configuration parameter "shared_bufers" in file "/etc/postgresql/16/main/conf.d/60-tuning.conf" line 3
   FATAL:  configuration file "/etc/postgresql/16/main/conf.d/60-tuning.conf" contains errors
   ```

3. Файл подключается через `include_dir = 'conf.d'` в конце `postgresql.conf`:

   ```bash
   grep include_dir /etc/postgresql/16/main/postgresql.conf
   cat /etc/postgresql/16/main/conf.d/60-tuning.conf
   ```

4. Конфигурацию можно проверить, не запуская сервер:

   ```bash
   runuser -u postgres -- /usr/lib/postgresql/16/bin/postgres \
     --config-file=/etc/postgresql/16/main/postgresql.conf -C shared_buffers
   ```

5. Если исправить только опечатку, запуск снова падает, уже с другой ошибкой:

   ```text
   FATAL:  could not map anonymous shared memory: Cannot allocate memory
   HINT:  This error usually means that PostgreSQL's request for a shared memory segment
   exceeded available memory, swap space, or huge pages. To reduce the request size
   (currently 8803336192 bytes), reduce PostgreSQL's shared memory usage ...
   ```

   Запрошено ~8,8 GB, а на ВМ (`free -m`) около 955 MiB.

Причина найдена, если названы обе ошибки в одной строке файла тюнинга, а остальные параметры релиза определены как корректные.

## Корневая причина

Файл тюнинга релиза 2026.11 подготовлен для другого класса серверов и не проверялся перед выкаткой: опечатка в имени параметра и `shared_buffers`, превышающий память ВМ.

## Исправление вручную

```bash
sed -i 's/^shared_bufers = 8GB$/shared_buffers = 256MB/' \
  /etc/postgresql/16/main/conf.d/60-tuning.conf
runuser -u postgres -- /usr/lib/postgresql/16/bin/postgres \
  --config-file=/etc/postgresql/16/main/postgresql.conf -C shared_buffers
systemctl restart postgresql@16-main
runuser -u postgres -- psql -c 'SHOW shared_buffers' -c 'SHOW work_mem' -c 'SHOW effective_cache_size'
```

Также допустимо закомментировать строку: тогда действует значение по умолчанию из `postgresql.conf` (`128MB`), а остальные параметры релиза сохраняются.

## Неправильные исправления

| Действие | Что происходит | Проверка |
|---|---|---|
| Исправить только опечатку | Кластер не стартует: не хватает памяти под 8 GB | падает: база недоступна |
| Удалить весь файл тюнинга | Кластер стартует, но `work_mem` и `effective_cache_size` возвращаются к значениям по умолчанию (`4MB`, `4GB`) | падает: параметры релиза потеряны |
| Подобрать `shared_buffers` «впритык», например `900MB` | Кластер стартует, но буфер занимает почти всю память ВМ | падает: больше половины памяти |

## Восстановление через исходный код упражнения

```bash
bin/incidentctl infra postgres-bad-config
```

Infra-playbook возвращает исправный `60-tuning.conf`, удаляет посторонние файлы из `conf.d/`, очищает настройки `ALTER SYSTEM`, проверяет конфигурацию через `postgres -C` и перезапускает кластер.

## Проверка решения

```bash
bin/incidentctl verify postgres-bad-config
```

Проверка выполняется один раз, без окна наблюдения: у этого инцидента нет фонового процесса, который мог бы вернуть поломку. Она требует:

- PostgreSQL принимает подключения;
- `work_mem = 16MB` и `effective_cache_size = 512MB` — значения из файла тюнинга;
- `shared_buffers` не больше половины `MemTotal` из `/proc/meminfo`;
- `orders-api` сохраняет заказ через `POST /orders` и читает его через `GET /orders/<id>`.

Ожидаемый результат:

```text
work_mem = 16MB
effective_cache_size = 512MB
shared_buffers = 256MB fits half of 955 MiB of memory.
The application stores and reads orders.
```

Значения измерены на ВМ Ubuntu 24.04.5, 1 vCPU, 1 GiB (Lima, Apple Virtualization.framework).
