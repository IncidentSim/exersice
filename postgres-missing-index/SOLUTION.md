# Решение инцидента «Отчёты по клиентам тормозят»

## Краткий ответ

Миграция `2026_11_10_cleanup_unused_indexes` удалила индекс `sales_customer_id_idx` по `sales(customer_id)`: на тестовой копии без отчётного трафика он выглядел неиспользуемым. Отчёт `GET /customers/<id>/sales` выполняет

```sql
SELECT count(*), coalesce(sum(amount), 0), max(sold_at) FROM sales WHERE customer_id = $1
```

и без индекса читает всю таблицу (12 млн строк, ~780 MB) ради ~120 строк клиента.

Исправление — вернуть индекс без блокировки записи и обновить статистику:

```sql
CREATE INDEX CONCURRENTLY sales_customer_id_idx ON sales (customer_id);
ANALYZE sales;
```

## Воспроизведение

```bash
bin/incidentctl incident postgres-missing-index
bin/incidentctl verify postgres-missing-index
```

```text
Customer 69885: expected HTTP 200 with 120 sales within 0.1 s, got 200 0.484210
```

Ответы правильные, но каждый занимает около 0,5 секунды вместо нескольких миллисекунд.

## Диагностика

1. Время уходит в базе. Медленные запросы видны в журнале (`log_min_duration_statement = 250ms`):

   ```bash
   time curl --silent http://127.0.0.1:8080/customers/4242/sales
   grep 'duration:' /var/log/postgresql/postgresql-16-main.log | tail -3
   ```

2. План запроса:

   ```bash
   runuser -u postgres -- psql incidentsim -c \
     "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*), sum(amount) FROM sales WHERE customer_id = 4242"
   ```

   ```text
   ->  Parallel Seq Scan on sales  (... rows=40 loops=3)
         Filter: (customer_id = 4242)
         Rows Removed by Filter: 3999960
         Buffers: shared hit=6840 read=93160
   Execution Time: 503.606 ms
   ```

   Чтобы найти 120 строк, читаются все 100 000 страниц таблицы.

3. Индексы таблицы:

   ```bash
   runuser -u postgres -- psql incidentsim -c '\d sales'
   ```

   Есть только первичный ключ `sales_pkey`; индекса по `customer_id` нет.

Причина найдена, если показано, что запрос отчёта выполняется последовательным сканированием всей таблицы и индекса по колонке фильтра нет.

## Корневая причина

Индекс по `customer_id` удалили как неиспользуемый, ориентируясь на статистику тестовой среды, где не было отчётных запросов.

## Исправление вручную

```bash
runuser -u postgres -- psql incidentsim \
  -c 'CREATE INDEX CONCURRENTLY sales_customer_id_idx ON sales (customer_id)' \
  -c 'ANALYZE sales'
```

На ВМ 1 vCPU / 1 GiB построение занимает около 5 секунд. `CONCURRENTLY` не блокирует запись в `sales`, поэтому подходит для работающего сервиса. Если построение прервётся, останется невалидный индекс (`\d sales` покажет `INVALID`): его нужно удалить (`DROP INDEX CONCURRENTLY`) и построить заново.

Проверить план после исправления:

```bash
runuser -u postgres -- psql incidentsim -c \
  "EXPLAIN (ANALYZE) SELECT count(*), sum(amount) FROM sales WHERE customer_id = 4242"
```

```text
->  Bitmap Heap Scan on sales  (actual time=0.153..3.032 rows=120 loops=1)
      ->  Bitmap Index Scan on sales_customer_id_idx  (actual time=0.114..0.114 rows=120 loops=1)
Execution Time: 3.085 ms
```

## Неправильные исправления

| Действие | Что происходит | Проверка |
|---|---|---|
| Увеличить `work_mem` | План не меняется, по-прежнему ~0,5 с | падает |
| Увеличить `statement_timeout` | Запросы и так не прерываются; скорость та же | падает |
| Перезапустить PostgreSQL | План не меняется | падает |
| Кэшировать ответы | Нарушает контракт; проверка каждый раз выбирает новых клиентов | падает |
| Обычный `CREATE INDEX` | Скорость вернётся, но запись в `sales` блокируется на время построения | проходит, но так делать в продакшене нельзя |

## Восстановление через исходный код упражнения

```bash
bin/incidentctl infra postgres-missing-index
```

Infra-playbook проверяет индекс `sales_customer_id_idx`: если он отсутствует, создаёт его, а невалидный — удаляет и строит заново, после чего выполняет `ANALYZE`.

## Проверка решения

```bash
bin/incidentctl verify postgres-missing-index
```

Проверка выбирает 5 случайных клиентов из 100 000 и для каждого:

- запрашивает `GET /customers/<id>/sales` и замеряет время ответа через `curl` (модуль `uri` даёт время только в целых секундах);
- сверяет число продаж с прямым `SELECT count(*)` в PostgreSQL;
- требует HTTP 200, совпадение числа продаж и время не больше 0,1 секунды.

Ожидаемый результат:

```text
Customer 5914: 120 sales in 0.005579 s.
```

## Параметры калибровки

Измерено на ВМ Ubuntu 24.04.5, 1 vCPU, 1 GiB (Lima, Apple Virtualization.framework).

| Параметр | Значение |
|---|---|
| Строк в `sales` | 12 000 000 (~120 на клиента, 100 000 клиентов) |
| Размер таблицы / индекса | 781 MB / 78 MB |
| Загрузка данных infra-playbook | ~11 с, построение индекса ~3 с |
| Отчёт без индекса | 0,46–0,53 с |
| Отчёт с индексом | 0,005–0,009 с (в том числе после сброса кэша ОС: 3–6 мс на запрос в базе) |
| Порог проверки | 0,1 с: запас ~5× до медленного плана и ~10× от быстрого |

При 3 млн строк (195 MB) запрос без индекса занимал ~90 мс — слишком близко к порогу, поэтому объём увеличен до 12 млн.
