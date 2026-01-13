#!/usr/bin/env python3
"""
Скрипт миграции для изменения типов user_id и manager_id на BigInteger
"""

import asyncio
from sqlalchemy import text
from modules.database import Database
from modules.config import Config

async def migrate():
    """Выполнить миграцию"""
    config = Config()
    db = Database(config)
    
    try:
        await db.initialize()
        print("Database connected")
        
        async with db.get_session() as session:
            # Изменяем тип user_id в таблице chats
            print("Migrating chats.user_id to BIGINT...")
            await session.execute(text("""
                ALTER TABLE chats 
                ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT
            """))
            
            # Изменяем тип manager_id в таблице chats
            print("Migrating chats.manager_id to BIGINT...")
            await session.execute(text("""
                ALTER TABLE chats 
                ALTER COLUMN manager_id TYPE BIGINT USING manager_id::BIGINT
            """))
            
            # Изменяем тип user_id в таблице messages
            print("Migrating messages.user_id to BIGINT...")
            await session.execute(text("""
                ALTER TABLE messages 
                ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT
            """))
            
            # Изменяем тип manager_id в таблице manager_notifications
            print("Migrating manager_notifications.manager_id to BIGINT...")
            await session.execute(text("""
                ALTER TABLE manager_notifications 
                ALTER COLUMN manager_id TYPE BIGINT USING manager_id::BIGINT
            """))
            
            await session.commit()
            print("Migration completed successfully!")
            
    except Exception as e:
        print(f"Migration error: {e}")
        raise
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(migrate())
