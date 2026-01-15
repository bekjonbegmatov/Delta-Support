# Схема базы данных для информации о сервисе

Для того чтобы AI бот получал информацию о сервисе из базы данных проекта (вместо .env), создайте таблицу `service_info` в вашей БД проекта.

## PostgreSQL

```sql
CREATE TABLE IF NOT EXISTS service_info (
    id SERIAL PRIMARY KEY,
    faq TEXT,
    tariffs TEXT,
    instructions TEXT,
    features TEXT,
    support_hours VARCHAR(255) DEFAULT 'Круглосуточно',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Вставьте данные
INSERT INTO service_info (faq, tariffs, instructions, features, support_hours) VALUES (
    'Как подключиться к VPN?
- Скачайте VPN клиент для вашего устройства
- Получите subscription URL в личном кабинете
- Добавьте URL в настройки клиента
- Подключитесь к серверу

Как оплатить подписку?
- Перейдите в раздел "Оплата" в личном кабинете
- Выберите тариф и способ оплаты
- Следуйте инструкциям на экране',

    'Basic - 100₽/месяц:
- 1 устройство
- Безлимитный трафик
- Серверы в 20 странах

Pro - 200₽/месяц:
- 3 устройства
- Безлимитный трафик
- Серверы в 50 странах
- Приоритетная поддержка

Elite - 300₽/месяц:
- Безлимит устройств
- Безлимитный трафик
- Все серверы
- Приоритетная поддержка 24/7',

    '1. Скачайте VPN клиент для вашего устройства
2. Зарегистрируйтесь или войдите в личный кабинет
3. Получите subscription URL в разделе "Конфиги"
4. Добавьте subscription URL в настройки VPN клиента
5. Выберите сервер и подключитесь
6. Готово! Ваше соединение защищено',

    'Безлимитный трафик
Поддержка всех популярных платформ (Windows, macOS, Linux, iOS, Android)
Серверы в 50+ странах
Высокая скорость соединения
Защита от утечек DNS
Kill Switch функция
Поддержка протоколов: WireGuard, OpenVPN, IKEv2',

    'Круглосуточно'
);
```

## SQLite

```sql
CREATE TABLE IF NOT EXISTS service_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    faq TEXT,
    tariffs TEXT,
    instructions TEXT,
    features TEXT,
    support_hours TEXT DEFAULT 'Круглосуточно',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Вставьте данные (аналогично PostgreSQL)
INSERT INTO service_info (faq, tariffs, instructions, features, support_hours) VALUES (
    'Ваш FAQ здесь...',
    'Ваши тарифы здесь...',
    'Ваши инструкции здесь...',
    'Ваши особенности здесь...',
    'Круглосуточно'
);
```

## Автоматическое получение тарифов

Если таблица `service_info` не содержит поле `tariffs`, бот автоматически попытается получить тарифы из таблицы `tariffs`:

```sql
-- Таблица tariffs (если используется)
CREATE TABLE tariffs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10,2),
    description TEXT,
    duration_days INTEGER,
    -- другие поля
);

-- Бот автоматически получит тарифы из этой таблицы
```

## Приоритет данных

1. **База данных проекта** (если подключена `PROJECT_DB_1/2/3`)
   - Таблица `service_info` (поля: faq, tariffs, instructions, features, support_hours)
   - Таблица `tariffs` (для автоматического получения тарифов)

2. **Переменные .env** (если данных нет в БД)
   - `SERVICE_FAQ`
   - `SERVICE_TARIFS`
   - `SERVICE_INSTRUCTIONS`
   - `SERVICE_FEATURES`
   - `SERVICE_SUPPORT_HOURS`

## Обновление информации

Чтобы обновить информацию в БД:

```sql
-- PostgreSQL
UPDATE service_info 
SET faq = 'Новый FAQ',
    updated_at = CURRENT_TIMESTAMP
WHERE id = 1;

-- SQLite
UPDATE service_info 
SET faq = 'Новый FAQ',
    updated_at = CURRENT_TIMESTAMP
WHERE id = 1;
```

После обновления перезапустите бота:
```bash
docker compose restart app
```

## Проверка

Проверьте, что информация получена из БД:

```bash
docker compose logs app | grep -i "service_info\|tariffs"
```
