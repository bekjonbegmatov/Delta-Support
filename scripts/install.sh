#!/usr/bin/env bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

say_header() {
  echo "=========================================="
  echo "$1"
  echo "=========================================="
  echo ""
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

SUDO=""
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  if need_cmd sudo; then
    SUDO="sudo"
  fi
fi

ensure_base_tools() {
  if need_cmd apt-get; then
    $SUDO apt-get update -y
    $SUDO apt-get install -y ca-certificates curl git
    if ! need_cmd python3; then
      $SUDO apt-get install -y python3
    fi
    if ! need_cmd openssl; then
      $SUDO apt-get install -y openssl
    fi
    return
  fi
  if need_cmd yum; then
    $SUDO yum install -y ca-certificates curl git python3 openssl
    return
  fi
  if need_cmd apk; then
    $SUDO apk add --no-cache ca-certificates curl git python3 openssl
    return
  fi
}

ensure_docker() {
  if need_cmd docker; then
    return
  fi
  say_header "Установка Docker"
  if [ -z "$SUDO" ] && [ "${EUID:-$(id -u)}" -ne 0 ]; then
    error "Нужны права root/sudo для установки Docker."
    exit 1
  fi
  ensure_base_tools
  curl -fsSL https://get.docker.com | $SUDO sh
  if need_cmd systemctl; then
    $SUDO systemctl enable --now docker || true
  fi
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    $SUDO usermod -aG docker "${USER:-$(id -un)}" || true
  fi
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    echo "docker"
    return
  fi
  if [ -n "$SUDO" ]; then
    echo "sudo docker"
    return
  fi
  echo "docker"
}

compose_cmd() {
  local D
  D="$(docker_cmd)"
  if $D compose version >/dev/null 2>&1; then
    echo "$D compose"
    return
  fi
  if need_cmd docker-compose; then
    if [ -n "$SUDO" ]; then
      echo "sudo docker-compose"
      return
    fi
    echo "docker-compose"
    return
  fi
  echo "$D compose"
}

TTY_IN=""
if [ -r /dev/tty ]; then
  TTY_IN="/dev/tty"
elif [ -t 0 ]; then
  TTY_IN="/dev/stdin"
fi

require_tty_or_env() {
  if [ -n "$TTY_IN" ]; then
    return
  fi
  error "Нет TTY для ввода. Запусти в интерактивной сессии или передай переменные окружения (TELEGRAM_BOT_TOKEN и т.д.)."
  exit 1
}

prompt() {
  local msg="$1"
  local def="${2:-}"
  local var
  require_tty_or_env
  if [ -n "$def" ]; then
    read -r -p "$msg [$def]: " var <"$TTY_IN"
    echo "${var:-$def}"
  else
    read -r -p "$msg: " var <"$TTY_IN"
    echo "$var"
  fi
}

prompt_secret() {
  local msg="$1"
  local var
  require_tty_or_env
  read -r -p "$msg: " var <"$TTY_IN"
  echo "$var"
}

generate_secret() {
  if need_cmd python3; then
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
    return
  fi
  if need_cmd openssl; then
    openssl rand -base64 48 | tr -d '\n' | cut -c1-43
    echo ""
    return
  fi
  date +%s | sha256sum | awk '{print $1}'
}

REPO_URL="${REPO_URL:-https://github.com/bekjonbegmatov/Delta-Support.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
DEFAULT_DIR="/opt/delta-supportdesk"
if [ -z "$SUDO" ] && [ "${EUID:-$(id -u)}" -ne 0 ]; then
  DEFAULT_DIR="${HOME:-/tmp}/delta-supportdesk"
fi
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"

say_header "Delta-SupportDesk: автоустановка"

ensure_base_tools
ensure_docker

COMPOSE="$(compose_cmd)"
info "Использую команду: $COMPOSE"

say_header "Клонирование репозитория"
if [ -d "$INSTALL_DIR/.git" ]; then
  info "Репозиторий уже существует: $INSTALL_DIR"
  cd "$INSTALL_DIR"
  git fetch --all --prune
  git reset --hard "origin/$REPO_BRANCH"
else
  if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    error "Путь занят файлом: $INSTALL_DIR"
    exit 1
  fi
  mkdir -p "$INSTALL_DIR"
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

if [ ! -f env.example ]; then
  error "env.example не найден в репозитории."
  exit 1
fi

say_header "Настройка .env"
if [ -f .env ]; then
  warn ".env уже существует — создаю резервную копию."
  cp .env ".env.backup.$(date +%Y%m%d_%H%M%S)"
fi
cp env.example .env

PROJECT_NAME="${PROJECT_NAME:-}"
PROJECT_DESCRIPTION="${PROJECT_DESCRIPTION:-}"
PROJECT_WEBSITE="${PROJECT_WEBSITE:-}"
PROJECT_BOT_LINK="${PROJECT_BOT_LINK:-}"
PROJECT_OWNER_CONTACTS="${PROJECT_OWNER_CONTACTS:-}"
if [ -z "$PROJECT_NAME" ]; then PROJECT_NAME="$(prompt "Название проекта" "DELTA-Support")"; fi
if [ -z "$PROJECT_DESCRIPTION" ]; then PROJECT_DESCRIPTION="$(prompt "Описание проекта" "")"; fi
if [ -z "$PROJECT_WEBSITE" ]; then PROJECT_WEBSITE="$(prompt "Сайт проекта (если есть)" "")"; fi
if [ -z "$PROJECT_BOT_LINK" ]; then PROJECT_BOT_LINK="$(prompt "Ссылка на бота (если есть)" "")"; fi
if [ -z "$PROJECT_OWNER_CONTACTS" ]; then PROJECT_OWNER_CONTACTS="$(prompt "Контакты владельца" "")"; fi

WEB_PORT="${WEB_PORT:-}"
if [ -z "$WEB_PORT" ]; then WEB_PORT="$(prompt "Порт веб-интерфейса" "3030")"; fi

say_header "Настройка Telegram"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then TELEGRAM_BOT_TOKEN="$(prompt_secret "Telegram Bot Token (обязательно)")"; fi
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
  error "TELEGRAM_BOT_TOKEN обязателен."
  exit 1
fi
TELEGRAM_ADMIN_IDS="${TELEGRAM_ADMIN_IDS:-}"
TELEGRAM_MANAGER_IDS="${TELEGRAM_MANAGER_IDS:-}"
if [ -z "$TELEGRAM_ADMIN_IDS" ]; then TELEGRAM_ADMIN_IDS="$(prompt "ID администраторов (через запятую)" "")"; fi
if [ -z "$TELEGRAM_MANAGER_IDS" ]; then TELEGRAM_MANAGER_IDS="$(prompt "ID менеджеров (через запятую)" "")"; fi

ENABLE_GROUP="${ENABLE_GROUP:-}"
if [ -z "$ENABLE_GROUP" ]; then ENABLE_GROUP="$(prompt "Включить режим группы поддержки (форум‑топики)? (y/n)" "n")"; fi
TELEGRAM_GROUP_MODE="false"
TELEGRAM_SUPPORT_GROUP_ID=""
if [ "$ENABLE_GROUP" = "y" ] || [ "$ENABLE_GROUP" = "Y" ]; then
  TELEGRAM_GROUP_MODE="true"
  TELEGRAM_SUPPORT_GROUP_ID="${TELEGRAM_SUPPORT_GROUP_ID:-}"
  if [ -z "$TELEGRAM_SUPPORT_GROUP_ID" ]; then TELEGRAM_SUPPORT_GROUP_ID="$(prompt "ID Telegram группы (с форумом)" "")"; fi
fi

say_header "Настройка AI"
AI_ENABLED="${AI_ENABLED:-}"
if [ -z "$AI_ENABLED" ]; then AI_ENABLED="$(prompt "Включить AI поддержку? (y/n)" "y")"; fi
AI_SUPPORT_ENABLED="false"
AI_SUPPORT_API_TYPE="groq"
AI_SUPPORT_API_KEY="${AI_SUPPORT_API_KEY:-}"
if [ "$AI_ENABLED" = "y" ] || [ "$AI_ENABLED" = "Y" ]; then
  AI_SUPPORT_ENABLED="true"
  AI_TYPE_CHOICE="${AI_TYPE_CHOICE:-}"
  if [ -z "$AI_TYPE_CHOICE" ]; then AI_TYPE_CHOICE="$(prompt "Тип AI API: 1) Groq  2) Rule-based" "1")"; fi
  if [ "$AI_TYPE_CHOICE" = "2" ]; then
    AI_SUPPORT_API_TYPE="rule-based"
    AI_SUPPORT_API_KEY=""
  else
    AI_SUPPORT_API_TYPE="groq"
    if [ -z "$AI_SUPPORT_API_KEY" ]; then AI_SUPPORT_API_KEY="$(prompt_secret "Groq API ключ")"; fi
  fi
fi

sanitize_one_line() {
  local v="${1:-}"
  v="${v//$'\r'/}"
  v="${v//$'\n'/}"
  echo "$v"
}

set_dotenv() {
  local key="$1"
  local value
  value="$(sanitize_one_line "${2:-}")"
  if [ -f .env ] && grep -q "^${key}=" .env; then
    local escaped
    escaped="$(printf '%s' "$value" | sed -e 's/[\\&/]/\\&/g')"
    sed -i "s/^${key}=.*/${key}=${escaped}/" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

PROJECT_NAME="$(sanitize_one_line "$PROJECT_NAME")"
PROJECT_DESCRIPTION="$(sanitize_one_line "$PROJECT_DESCRIPTION")"
PROJECT_WEBSITE="$(sanitize_one_line "$PROJECT_WEBSITE")"
PROJECT_BOT_LINK="$(sanitize_one_line "$PROJECT_BOT_LINK")"
PROJECT_OWNER_CONTACTS="$(sanitize_one_line "$PROJECT_OWNER_CONTACTS")"
TELEGRAM_BOT_TOKEN="$(sanitize_one_line "$TELEGRAM_BOT_TOKEN")"
TELEGRAM_ADMIN_IDS="$(sanitize_one_line "$TELEGRAM_ADMIN_IDS")"
TELEGRAM_MANAGER_IDS="$(sanitize_one_line "$TELEGRAM_MANAGER_IDS")"
TELEGRAM_GROUP_MODE="$(sanitize_one_line "$TELEGRAM_GROUP_MODE")"
TELEGRAM_SUPPORT_GROUP_ID="$(sanitize_one_line "$TELEGRAM_SUPPORT_GROUP_ID")"
AI_SUPPORT_ENABLED="$(sanitize_one_line "$AI_SUPPORT_ENABLED")"
AI_SUPPORT_API_TYPE="$(sanitize_one_line "$AI_SUPPORT_API_TYPE")"
AI_SUPPORT_API_KEY="$(sanitize_one_line "$AI_SUPPORT_API_KEY")"
WEB_PORT="$(sanitize_one_line "$WEB_PORT")"

JWT_SECRET_KEY="$(generate_secret)"
POSTGRES_USER="delta_support"
POSTGRES_DB="delta_support"
POSTGRES_PASSWORD="$(generate_secret | tr -d '=+/' | cut -c1-25)"
DATABASE_URL_CONTAINER="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}"

set_dotenv PROJECT_NAME "$PROJECT_NAME"
set_dotenv PROJECT_DESCRIPTION "$PROJECT_DESCRIPTION"
set_dotenv PROJECT_WEBSITE "$PROJECT_WEBSITE"
set_dotenv PROJECT_BOT_LINK "$PROJECT_BOT_LINK"
set_dotenv PROJECT_OWNER_CONTACTS "$PROJECT_OWNER_CONTACTS"
set_dotenv WEB_PORT "$WEB_PORT"
set_dotenv TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
set_dotenv TELEGRAM_ADMIN_IDS "$TELEGRAM_ADMIN_IDS"
set_dotenv TELEGRAM_MANAGER_IDS "$TELEGRAM_MANAGER_IDS"
set_dotenv TELEGRAM_GROUP_MODE "$TELEGRAM_GROUP_MODE"
set_dotenv TELEGRAM_SUPPORT_GROUP_ID "$TELEGRAM_SUPPORT_GROUP_ID"
set_dotenv AI_SUPPORT_ENABLED "$AI_SUPPORT_ENABLED"
set_dotenv AI_SUPPORT_API_TYPE "$AI_SUPPORT_API_TYPE"
set_dotenv AI_SUPPORT_API_KEY "$AI_SUPPORT_API_KEY"
set_dotenv POSTGRES_USER "$POSTGRES_USER"
set_dotenv POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
set_dotenv POSTGRES_DB "$POSTGRES_DB"
set_dotenv JWT_SECRET_KEY "$JWT_SECRET_KEY"
set_dotenv DATABASE_URL_CONTAINER "$DATABASE_URL_CONTAINER"

mkdir -p data logs web/static/uploads/branding

say_header "Сборка и запуск контейнеров"
$COMPOSE down 2>/dev/null || true
$COMPOSE up -d --build

info "Ожидаю готовности сервиса..."
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:${WEB_PORT}/api/branding" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

say_header "Статус"
$COMPOSE ps || true

PUBLIC_IP=""
if need_cmd curl; then
  PUBLIC_IP="$(curl -fsSL https://api.ipify.org || true)"
fi
if [ -z "$PUBLIC_IP" ]; then
  PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi
if [ -z "$PUBLIC_IP" ]; then
  PUBLIC_IP="SERVER_IP"
fi

echo ""
info "Веб-интерфейс: http://${PUBLIC_IP}:${WEB_PORT}/"
info "Логин/пароль по умолчанию: admin / admin123"
warn "Сразу после входа поменяйте пароль пользователя admin."
echo ""
