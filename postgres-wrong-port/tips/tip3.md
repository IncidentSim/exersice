# Подсказка 3. Чинить сервер, а не клиента

Адрес `127.0.0.1:5432` — часть контракта, переносить приложение на другой порт нельзя. Найди, где задан порт: `sudo -u postgres psql -c "SELECT name, setting, sourcefile, sourceline FROM pg_settings WHERE name IN ('port', 'listen_addresses')"`. Для смены порта нужен перезапуск кластера, а не `reload`.
