# Подсказка 2. Откуда берутся настройки?

Кроме `postgresql.conf`, PostgreSQL читает файлы из каталога `conf.d/` (директива `include_dir`) и `postgresql.auto.conf`. Найди файл и строку, на которые ссылается ошибка. Проверить конфигурацию без запуска сервера можно так: `sudo -u postgres /usr/lib/postgresql/16/bin/postgres --config-file=/etc/postgresql/16/main/postgresql.conf -C shared_buffers`.
