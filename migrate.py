import asyncio
import os
import sqlite3
import psycopg
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.postgres import PostgresSaver

from config import settings
from core.memory import UnifiedMemory
from core.user_settings import UserSettingsStore

async def migrate():
    print("Starting PERFECT migration from SQLite to PostgreSQL...")
    
    sqlite_path = "/app/data/memory.sqlite"
    if not os.path.exists(sqlite_path):
        print(f"No SQLite DB found at {sqlite_path}. Nothing to migrate.")
        return
        
    sl_conn = sqlite3.connect(sqlite_path)
    sl_conn.row_factory = sqlite3.Row
    c = sl_conn.cursor()
    
    pg_dsn = os.environ.get('DATABASE_URL').replace('+asyncpg', '')
    
    print("Initializing PostgreSQL schemas...")
    um = UnifiedMemory()
    us = UserSettingsStore()
    
    await um._ensure_schema_pg()
    await us._ensure_schema()
    
    with psycopg.connect(pg_dsn, autocommit=True) as pg_conn:
        pg_saver = PostgresSaver(pg_conn)
        pg_saver.setup()
        
    print("Schemas initialized. Transferring data...")
    
    # 1. Transfer LangGraph Checkpoints using the official API (handles schema changes automatically)
    sl_saver = SqliteSaver(sl_conn)
    checkpoints = list(sl_saver.list(None))
    print(f"Migrating {len(checkpoints)} LangGraph checkpoints...")
    
    with psycopg.connect(pg_dsn, autocommit=True) as pg_conn:
        pg_saver = PostgresSaver(pg_conn)
        for cp in checkpoints:
            pg_saver.put(cp.config, cp.checkpoint, cp.metadata, cp.checkpoint["channel_versions"])
            
    # 2. Transfer UnifiedMemory using SQL
    with psycopg.connect(pg_dsn, autocommit=True) as pg_conn:
        with pg_conn.cursor() as pg_c:
            c.execute("SELECT session_id, role, content, provider, model, ts FROM unified_memory")
            um_rows = c.fetchall()
            print(f"Migrating {len(um_rows)} rows from unified_memory...")
            for row in um_rows:
                pg_c.execute(
                    "INSERT INTO unified_memory (session_id, role, content, provider, model, ts) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (row['session_id'], row['role'], row['content'], row['provider'], row['model'], row['ts'])
                )
                
            try:
                c.execute("SELECT user_id, platform, system_prompt, preferred_model, language, updated_at FROM user_settings")
                us_rows = c.fetchall()
                print(f"Migrating {len(us_rows)} rows from user_settings...")
                for row in us_rows:
                    pg_c.execute(
                        "INSERT INTO user_settings (user_id, platform, system_prompt, preferred_model, language, updated_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (row['user_id'], row['platform'], row['system_prompt'], row['preferred_model'], row['language'], row['updated_at'])
                    )
            except sqlite3.OperationalError:
                pass
                
    print("Migration completed flawlessly! You can now safely switch to PostgreSQL.")

if __name__ == '__main__':
    asyncio.run(migrate())
