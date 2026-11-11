# Подсказка 2. Кто занял соединения?

Суперпользователь может подключиться даже тогда, когда обычным ролям места не осталось. Посмотри, кто держит сессии:

`runuser -u postgres -- psql -c "SELECT usename, application_name, state, count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1, 2, 3"`

Повтори запрос через минуту и сравни.
