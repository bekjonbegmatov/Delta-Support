# Руководство по подключению внешних баз данных

## Подключение базы данных из другого Docker контейнера

Если ваша база данных находится в другом Docker контейнере, есть несколько способов подключения:

### Вариант 1: База данных на хосте (localhost)

Если база данных запущена на хосте (не в Docker), используйте `host.docker.internal`:

```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@host.docker.internal:5432/stealthnet
```

### Вариант 2: База данных в другом Docker контейнере (через имя контейнера)

Если база данных в другом контейнере, нужно подключить контейнеры к одной сети Docker.

**Шаг 1:** Найдите имя контейнера с базой данных:
```bash
docker ps | grep postgres
```

**Шаг 2:** Найдите сеть этого контейнера:
```bash
docker inspect <container_name> | grep NetworkMode
```

**Шаг 3:** Подключите контейнер STELS-Support к той же сети. Отредактируйте `docker-compose.yml`:

```yaml
networks:
  stels-network:
    driver: bridge
  external_network:
    external: true
    name: <имя_сети_где_находится_БД>  # например: remnawave-network
```

И добавьте сеть к сервису app:
```yaml
app:
  # ... остальные настройки
  networks:
    - stels-network
    - external_network  # добавляем внешнюю сеть
```

**Шаг 4:** Используйте имя контейнера вместо localhost в .env:
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@<имя_контейнера_БД>:5432/stealthnet
```

Например, если контейнер называется `stealthnet-db`:
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@stealthnet-db:5432/stealthnet
```

### Вариант 3: База данных доступна по IP хоста

Если база данных доступна по IP адресу хоста:

```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@172.17.0.1:5432/stealthnet
```

Или используйте IP адрес Docker bridge:
```bash
# Узнать IP хоста из контейнера
docker exec stels-support-app ip route | grep default
```

### Вариант 4: Использование network_mode: host (не рекомендуется)

Можно запустить контейнер в режиме host сети, тогда localhost будет работать:

```yaml
app:
  network_mode: host
  # ... остальные настройки
```

Тогда в .env можно использовать:
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@localhost:5432/stealthnet
```

## Проверка подключения

После настройки проверьте подключение:

```bash
# Войдите в контейнер
docker compose exec app bash

# Проверьте подключение (если установлен psql)
psql "postgresql://stealthnet:stealthnet_secure_password_2025@host.docker.internal:5432/stealthnet" -c "SELECT 1;"
```

## Примеры для разных случаев

### База данных на хосте (порт 5432)
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@host.docker.internal:5432/stealthnet
```

### База данных в контейнере с именем "stealthnet-db"
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@stealthnet-db:5432/stealthnet
```

### База данных в контейнере на другом порту
```env
PROJECT_DB_1=postgresql://stealthnet:stealthnet_secure_password_2025@stealthnet-db:5433/stealthnet
```

### SQLite база данных (если файл доступен через volume)
```env
PROJECT_DB_1=sqlite:///data/stealthnet.db
```

И добавьте volume в docker-compose.yml:
```yaml
app:
  volumes:
    - ./data:/app/data
    - /path/to/stealthnet.db:/app/data/stealthnet.db:ro
```
