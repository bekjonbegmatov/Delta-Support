import asyncio
from modules.database import Database, AdminUser
from modules.config import Config
from web.utils import get_password_hash
from loguru import logger

async def create_admin():
    config = Config()
    db = Database(config)
    await db.initialize()
    
    import sys
    
    if len(sys.argv) > 2:
        username = sys.argv[1]
        password = sys.argv[2]
    else:
        username = input("Enter admin username: ")
        password = input("Enter admin password: ")
    
    # Check if exists
    if await AdminUser.get_or_none(username=username):
        logger.error(f"User {username} already exists!")
        return
    
    await AdminUser.create(
        username=username,
        password_hash=get_password_hash(password),
        role="admin"
    )
    logger.info(f"Admin user {username} created successfully!")

if __name__ == "__main__":
    asyncio.run(create_admin())
