# Примеры подключения внешних баз данных

## Ваш случай: База данных в другом Docker контейнере

### Если база данных на хосте (localhost:5432)

В файле `.env` используйте:

```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@host.docker.internal:5432/stealthnet
```

**Важно:** `host.docker.internal` работает на Linux только если Docker запущен с флагом `--add-host=host.docker.internal:host-gateway` или в Docker Desktop.

### Если база данных в другом Docker контейнере

**Шаг 1:** Найдите имя контейнера с базой данных:
```bash
docker ps | grep postgres
```

**Шаг 2:** Найдите сеть этого контейнера:
```bash
docker inspect <имя_контейнера> | grep -A 5 "Networks"
```

**Шаг 3:** Отредактируйте `docker-compose.yml` - добавьте внешнюю сеть:

```yaml
networks:
  stels-network:
    driver: bridge
  external_network:
    external: true
    name: remnawave-network  # замените на имя вашей сети
```

И добавьте сеть к сервису app:
```yaml
app:
  networks:
    - stels-network
    - external_network
```

**Шаг 4:** В `.env` используйте имя контейнера вместо localhost:

```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@<имя_контейнера>:5432/stealthnet
```

Например, если контейнер называется `stealthnet-db`:
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@stealthnet-db:5432/stealthnet
```

### Быстрое решение (если БД на хосте)

Если ваша база данных запущена на хосте (не в Docker), просто используйте:

```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@host.docker.internal:5432/stealthnet
```

И перезапустите контейнер:
```bash
docker compose restart app
```

### Проверка подключения

После настройки проверьте логи:
```bash
docker compose logs app | grep -i "database\|error\|connection"
```
