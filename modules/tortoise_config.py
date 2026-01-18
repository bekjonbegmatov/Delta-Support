import os

db_url = os.getenv("DATABASE_URL", "sqlite://db.sqlite3")
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgres://")

TORTOISE_ORM = {
    "connections": {"default": db_url},
    "apps": {
        "models": {
            "models": ["modules.database", "aerich.models"],
            "default_connection": "default",
        },
    },
}

