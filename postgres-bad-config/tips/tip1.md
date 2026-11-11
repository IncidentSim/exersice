# Подсказка 1. Почему сервис не стартует?

`systemctl status` скажет только, что запуск не удался. Причину PostgreSQL пишет в свой журнал: `/var/log/postgresql/postgresql-16-main.log`, а также в `journalctl -u postgresql@16-main`.
