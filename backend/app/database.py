import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
ROOT = Path(__file__).resolve().parents[2]
def database_path():
    path = Path(os.getenv('APP_DB_PATH','data/recipe_platform.db'))
    return path if path.is_absolute() else ROOT/path
@contextmanager
def connect():
    path=database_path()
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=15)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()
def migrate():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        versions={r[0] for r in db.execute('SELECT version FROM schema_migrations')} if exists else set()
        for file in sorted((ROOT/'backend/migrations').glob('*.sql')):
            if int(file.name.split('_')[0]) not in versions:
                db.executescript('BEGIN IMMEDIATE;\n'+file.read_text(encoding='utf-8')+'\nCOMMIT;')
