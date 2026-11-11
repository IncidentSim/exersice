# Подсказка 1. Запрос не падает — он ждёт

Если запрос висит без ошибки, его что-то держит. Посмотри, что сейчас делают сессии базы: `sudo -u postgres psql incidentsim -c "SELECT pid, usename, application_name, state, wait_event_type, wait_event, left(query, 60) FROM pg_stat_activity WHERE datname = 'incidentsim'"`. Журнал `/var/log/postgresql/postgresql-16-main.log` тоже сообщает о долгих ожиданиях.
