#!/bin/bash

# DELTA-Support Installation Script
# Автоматическая установка бота поддержки

set -e

echo "=========================================="
echo "DELTA-Support Installation Script"
echo "=========================================="
echo ""

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка наличия Docker
if ! command -v docker &> /dev/null; then
    error "Docker не установлен. Установите Docker и повторите попытку."
    exit 1
fi

# Проверка Docker Compose (поддержка обоих вариантов: docker-compose и docker compose)
DOCKER_COMPOSE_CMD=""
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
    info "Найден docker-compose (legacy)"
elif docker compose version &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
    info "Найден docker compose (plugin)"
else
    error "Docker Compose не установлен. Установите Docker Compose и повторите попытку."
    exit 1
fi

info "Docker и Docker Compose найдены"

# Переход в директорию проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

info "Рабочая директория: $PROJECT_DIR"

# Создание .env файла
if [ -f .env ]; then
    warn "Файл .env уже существует. Создаю резервную копию..."
    cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
fi

# Копирование примера
if [ -f env.example ]; then
    cp env.example .env
    info "Создан файл .env из примера"
else
    error "Файл env.example не найден!"
    exit 1
fi

echo ""
echo "=========================================="
echo "Настройка проекта"
echo "=========================================="
echo ""

# Запрос информации о проекте
read -p "Название проекта [DELTA-Support]: " PROJECT_NAME
PROJECT_NAME=${PROJECT_NAME:-DELTA-Support}

read -p "Описание проекта: " PROJECT_DESCRIPTION

read -p "Ссылка на сайт проекта (если есть): " PROJECT_WEBSITE

read -p "Ссылка на бот проекта (если есть): " PROJECT_BOT_LINK

read -p "Контакты владельца проекта: " PROJECT_OWNER_CONTACTS

echo ""
echo "=========================================="
echo "Настройка AI"
echo "=========================================="
echo ""

read -p "Включить AI поддержку? (y/n) [y]: " AI_ENABLED
AI_ENABLED=${AI_ENABLED:-y}

if [ "$AI_ENABLED" = "y" ] || [ "$AI_ENABLED" = "Y" ]; then
    echo "Выберите тип AI API:"
    echo "1) Groq (рекомендуется)"
    echo "2) Rule-based (без внешнего API)"
    read -p "Ваш выбор [1]: " AI_TYPE_CHOICE
    AI_TYPE_CHOICE=${AI_TYPE_CHOICE:-1}
    
    case $AI_TYPE_CHOICE in
        1)
            AI_TYPE="groq"
            read -p "Введите Groq API ключ: " AI_API_KEY
            ;;
        2)
            AI_TYPE="rule-based"
            AI_API_KEY=""
            ;;
        *)
            AI_TYPE="groq"
            read -p "Введите Groq API ключ: " AI_API_KEY
            ;;
    esac
else
    AI_TYPE="groq"
    AI_API_KEY=""
fi

echo ""
echo "=========================================="
echo "Настройка Telegram бота"
echo "=========================================="
echo ""

read -p "Telegram Bot Token: " TELEGRAM_BOT_TOKEN

if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    error "Telegram Bot Token обязателен!"
    exit 1
fi

read -p "ID администраторов (через запятую): " TELEGRAM_ADMIN_IDS
read -p "ID менеджеров (через запятую): " TELEGRAM_MANAGER_IDS

read -p "Включить режим группы поддержки (форум-топики)? (y/n) [n]: " ENABLE_GROUP
ENABLE_GROUP=${ENABLE_GROUP:-n}
TELEGRAM_GROUP_MODE="false"
TELEGRAM_SUPPORT_GROUP_ID=""
if [ "$ENABLE_GROUP" = "y" ] || [ "$ENABLE_GROUP" = "Y" ]; then
    TELEGRAM_GROUP_MODE="true"
    read -p "ID Telegram группы (с форумом): " TELEGRAM_SUPPORT_GROUP_ID
fi

echo ""
echo "=========================================="
echo "Настройка базы данных проектов"
echo "=========================================="
echo ""

read -p "Добавить базу данных проекта? (y/n) [n]: " ADD_PROJECT_DB
ADD_PROJECT_DB=${ADD_PROJECT_DB:-n}

PROJECT_DB_1=""
PROJECT_DB_2=""
PROJECT_DB_3=""

if [ "$ADD_PROJECT_DB" = "y" ] || [ "$ADD_PROJECT_DB" = "Y" ]; then
    echo "Формат: postgresql://user:password@host:port/dbname"
    echo "Или: sqlite:///path/to/database.db"
    read -p "База данных проекта 1: " PROJECT_DB_1
    read -p "База данных проекта 2 (опционально): " PROJECT_DB_2
    read -p "База данных проекта 3 (опционально): " PROJECT_DB_3
fi

echo ""
echo "=========================================="
echo "Генерация секретных ключей"
echo "=========================================="
echo ""

# Генерация JWT секрета
if command -v python3 &> /dev/null; then
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    info "JWT секрет сгенерирован"
else
    warn "Python3 не найден, используйте случайную строку для JWT_SECRET_KEY"
    read -p "Введите JWT Secret Key (минимум 32 символа): " JWT_SECRET
fi

# Генерация пароля для PostgreSQL
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
POSTGRES_USER="delta_support"
POSTGRES_DB="delta_support"

# Обновление .env файла
info "Обновление файла .env..."

sed -i "s|^PROJECT_NAME=.*|PROJECT_NAME=$PROJECT_NAME|" .env
sed -i "s|^PROJECT_DESCRIPTION=.*|PROJECT_DESCRIPTION=$PROJECT_DESCRIPTION|" .env
sed -i "s|^PROJECT_WEBSITE=.*|PROJECT_WEBSITE=$PROJECT_WEBSITE|" .env
sed -i "s|^PROJECT_BOT_LINK=.*|PROJECT_BOT_LINK=$PROJECT_BOT_LINK|" .env
sed -i "s|^PROJECT_OWNER_CONTACTS=.*|PROJECT_OWNER_CONTACTS=$PROJECT_OWNER_CONTACTS|" .env

if [ "$AI_ENABLED" = "y" ] || [ "$AI_ENABLED" = "Y" ]; then
    sed -i "s|^AI_SUPPORT_ENABLED=.*|AI_SUPPORT_ENABLED=true|" .env
else
    sed -i "s|^AI_SUPPORT_ENABLED=.*|AI_SUPPORT_ENABLED=false|" .env
fi

sed -i "s|^AI_SUPPORT_API_TYPE=.*|AI_SUPPORT_API_TYPE=$AI_TYPE|" .env
sed -i "s|^AI_SUPPORT_API_KEY=.*|AI_SUPPORT_API_KEY=$AI_API_KEY|" .env

sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN|" .env
sed -i "s|^TELEGRAM_ADMIN_IDS=.*|TELEGRAM_ADMIN_IDS=$TELEGRAM_ADMIN_IDS|" .env
sed -i "s|^TELEGRAM_MANAGER_IDS=.*|TELEGRAM_MANAGER_IDS=$TELEGRAM_MANAGER_IDS|" .env

if grep -q "^TELEGRAM_GROUP_MODE=" .env; then
    sed -i "s|^TELEGRAM_GROUP_MODE=.*|TELEGRAM_GROUP_MODE=$TELEGRAM_GROUP_MODE|" .env
else
    echo "TELEGRAM_GROUP_MODE=$TELEGRAM_GROUP_MODE" >> .env
fi
if [ -n "$TELEGRAM_SUPPORT_GROUP_ID" ]; then
    if grep -q "^TELEGRAM_SUPPORT_GROUP_ID=" .env; then
        sed -i "s|^TELEGRAM_SUPPORT_GROUP_ID=.*|TELEGRAM_SUPPORT_GROUP_ID=$TELEGRAM_SUPPORT_GROUP_ID|" .env
    else
        echo "TELEGRAM_SUPPORT_GROUP_ID=$TELEGRAM_SUPPORT_GROUP_ID" >> .env
    fi
fi

sed -i "s|^POSTGRES_USER=.*|POSTGRES_USER=$POSTGRES_USER|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$POSTGRES_PASSWORD|" .env
sed -i "s|^POSTGRES_DB=.*|POSTGRES_DB=$POSTGRES_DB|" .env
sed -i "s|postgresql://.*@postgres:5432/delta_support|postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB|" .env

sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$JWT_SECRET|" .env

if [ -n "$PROJECT_DB_1" ]; then
    sed -i "s|^PROJECT_DB_1=.*|PROJECT_DB_1=$PROJECT_DB_1|" .env
fi
if [ -n "$PROJECT_DB_2" ]; then
    sed -i "s|^PROJECT_DB_2=.*|PROJECT_DB_2=$PROJECT_DB_2|" .env
fi
if [ -n "$PROJECT_DB_3" ]; then
    sed -i "s|^PROJECT_DB_3=.*|PROJECT_DB_3=$PROJECT_DB_3|" .env
fi

info "Файл .env обновлен"

echo ""
echo "=========================================="
echo "Сборка и запуск Docker контейнеров"
echo "=========================================="
echo ""

# Создание директорий
mkdir -p data logs
chmod 755 data logs

info "Создание необходимых директорий..."

# Сборка и запуск
info "Запуск Docker Compose..."

$DOCKER_COMPOSE_CMD down 2>/dev/null || true
$DOCKER_COMPOSE_CMD build
$DOCKER_COMPOSE_CMD up -d

info "Ожидание запуска сервисов..."
sleep 10

# Проверка статуса
if $DOCKER_COMPOSE_CMD ps | grep -q "Up"; then
    info "✅ Сервисы успешно запущены!"
    echo ""
    echo "=========================================="
    echo "Установка завершена!"
    echo "=========================================="
    echo ""
    echo "Проверьте статус: $DOCKER_COMPOSE_CMD ps"
    echo "Просмотр логов: $DOCKER_COMPOSE_CMD logs -f"
    echo ""
    info "Бот готов к работе!"
else
    error "Ошибка при запуске сервисов. Проверьте логи: $DOCKER_COMPOSE_CMD logs"
    exit 1
fi
