FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода приложения
COPY . .

# Создание директорий для данных и логов
RUN mkdir -p /app/data /app/logs

# Установка прав
RUN chmod +x /app/scripts/*.sh 2>/dev/null || true

# Порт приложения
EXPOSE 8080

# Команда запуска
CMD ["python", "main.py"]
