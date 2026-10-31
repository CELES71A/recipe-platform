import hashlib
import hmac
import secrets
import time
from fastapi import Cookie, HTTPException, Request
from .database import connect

def password_hash(password):
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex()
    return f'{salt}${digest}'

def verify_password(password,stored):
    salt,digest=stored.split('$',1)
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex(),digest)

def current_user(request: Request,session: str | None=Cookie(default=None)):
    if not session:
        raise HTTPException(401,'请先登录')
    with connect() as db:
        row=db.execute('SELECT u.id,u.email,u.role FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(hashlib.sha256(session.encode()).hexdigest(),int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401,'登录已过期')
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'不允许跨站写入')
        if request.headers.get('x-requested-with')!='recipe-platform':
            raise HTTPException(403,'缺少请求校验头')
    return dict(row)
