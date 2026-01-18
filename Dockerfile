FROM node:20-alpine AS frontend_builder
WORKDIR /build
COPY web/frontend/package.json web/frontend/package-lock.json web/frontend/
RUN cd web/frontend && npm ci
COPY web/frontend web/frontend
RUN npm --prefix web/frontend run build

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    bash \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend_builder /build/web/static/spa /app/web/static/spa

RUN mkdir -p /app/data /app/logs /app/web/static/uploads/branding
RUN chmod +x /app/scripts/*.sh 2>/dev/null || true
RUN chmod +x /app/scripts/entrypoint.sh

EXPOSE 8080

CMD ["/app/scripts/entrypoint.sh"]
