"""Create or promote a local admin; password is read without echo."""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.database import connect,migrate
from app.security import password_hash
parser=argparse.ArgumentParser()
parser.add_argument('email')
args=parser.parse_args()
password=getpass.getpass('Admin password (at least 8 characters): ')
if len(password)<8:
    parser.error('Password must contain at least 8 characters')
migrate()
with connect() as db:
    db.execute("INSERT INTO users(email,password_hash,role) VALUES(?,?,'admin') ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash,role='admin'",(args.email.strip().lower(),password_hash(password)))
print('Administrator ready. Sign in through the website.')
