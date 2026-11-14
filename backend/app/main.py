import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .database import connect, migrate, ROOT
from .security import current_user, password_hash, verify_password

@asynccontextmanager
async def lifespan(app):
    migrate()
    yield
app=FastAPI(title='食谱与烹饪助手',version='0.2.0',lifespan=lifespan)

@app.middleware('http')
async def identify(request,call_next):
    request.state.request_id=uuid.uuid4().hex
    if request.method not in ('GET','HEAD','OPTIONS') and request.url.path.startswith('/api/'):
        origin=request.headers.get('origin')
        if request.headers.get('x-requested-with')!='recipe-platform' or (origin and origin!=str(request.base_url).rstrip('/')):
            return JSONResponse(status_code=403,content={'code':'CSRF_REJECTED','message':'请求来源校验失败','request_id':request.state.request_id})
    response=await call_next(request)
    response.headers['X-Request-ID']=request.state.request_id
    return response
@app.exception_handler(HTTPException)
async def http_error(request,error):
    return JSONResponse(status_code=error.status_code,content={'code':f'HTTP_{error.status_code}','message':str(error.detail),'request_id':request.state.request_id})
@app.exception_handler(RequestValidationError)
async def invalid(request,error):
    return JSONResponse(status_code=422,content={'code':'VALIDATION_ERROR','message':'请检查必填项、长度和数值范围','request_id':request.state.request_id})
@app.exception_handler(sqlite3.IntegrityError)
async def conflict(request,error):
    return JSONResponse(status_code=409,content={'code':'CONFLICT','message':'数据重复或不符合约束','request_id':request.state.request_id})

@app.exception_handler(sqlite3.OperationalError)
async def database_unavailable(request,error):
    return JSONResponse(status_code=503,content={'code':'DATABASE_UNAVAILABLE','message':'数据库暂时不可用，请确认 DB Browser 没有未提交的写事务，稍后重试','request_id':request.state.request_id})

def published(db,recipe_id):
    row=db.execute("SELECT * FROM recipes WHERE id=? AND status='published'",(recipe_id,)).fetchone()
    if not row:
        raise HTTPException(404,'食谱不存在或已下架')
    return row

@app.get('/api/v1/health')
def health():
    with connect() as db:
        db.execute('SELECT 1')
    return {'status':'ok'}
@app.get('/api/v1/stats')
def stats():
    with connect() as db:
        return {t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('recipes','source_reviews','ingredients')}
@app.get('/api/v1/categories')
def categories():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT id,name FROM categories ORDER BY name')]
@app.get('/api/v1/recipes')
def recipes(q: str=Query('',max_length=100),category_id: int | None=None,max_minutes: int | None=Query(None,ge=1,le=10080),min_rating: float | None=Query(None,ge=1,le=5),sort: str='id',page: int=Query(1,ge=1),page_size: int=Query(24,ge=1,le=100)):
    where,values=["r.status='published'"],[]
    if q.strip():
        tokens=['"'+word.replace('"','""')+'"' for word in q.split()]
        where.append('r.id IN (SELECT rowid FROM recipe_search WHERE recipe_search MATCH ?)')
        values.append(' AND '.join(tokens))
    if category_id is not None:
        where.append('r.category_id=?'); values.append(category_id)
    if max_minutes is not None:
        where.append('r.total_seconds<=?'); values.append(max_minutes*60)
    if min_rating is not None:
        where.append('r.source_rating>=?'); values.append(min_rating)
    order={'id':'r.id','rating':'r.source_rating DESC,r.id','time':'r.total_seconds IS NULL,r.total_seconds,r.id'}.get(sort)
    if not order:
        raise HTTPException(422,'排序参数无效')
    clause=' AND '.join(where)
    with connect() as db:
        total=db.execute('SELECT count(*) FROM recipes r WHERE '+clause,values).fetchone()[0]
        rows=db.execute('SELECT r.id,r.name,r.description,r.image_url,r.total_seconds,r.source_rating,c.name category_name FROM recipes r LEFT JOIN categories c ON c.id=r.category_id WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',values+[page_size,(page-1)*page_size]).fetchall()
    return {'items':[dict(r) for r in rows],'total':total,'page':page,'page_size':page_size}
@app.get('/api/v1/recipes/{recipe_id}')
def detail(recipe_id: int):
    with connect() as db:
        result=dict(published(db,recipe_id))
        result['steps']=[dict(r) for r in db.execute('SELECT position,instruction FROM recipe_steps WHERE recipe_id=? ORDER BY position',(recipe_id,))]
        result['ingredients']=[dict(r) for r in db.execute('SELECT i.name,ri.quantity FROM recipe_ingredients ri JOIN ingredients i ON i.id=ri.ingredient_id WHERE ri.recipe_id=? ORDER BY ri.position',(recipe_id,))]
        result['reviews']=[dict(r) for r in db.execute('SELECT v.id,v.rating,v.body FROM reviews v WHERE recipe_id=? ORDER BY v.id DESC LIMIT 20',(recipe_id,))]
        result['local_rating']=dict(db.execute('SELECT count(rating) count,avg(rating) average FROM reviews WHERE recipe_id=?',(recipe_id,)).fetchone())
        result['source_reviews']=[dict(r) for r in db.execute('SELECT id,rating,body,source_author_id FROM source_reviews WHERE recipe_id=? ORDER BY id DESC LIMIT 20',(recipe_id,))]
        result['nutrition']=json.loads(result.pop('nutrition_json') or '{}')
    return result

class Credentials(BaseModel):
    email: str=Field(min_length=3,max_length=254)
    password: str=Field(min_length=8,max_length=128)
def session_response(response,user):
    token=secrets.token_urlsafe(32)
    with connect() as db:
        db.execute('DELETE FROM auth_sessions WHERE expires_at<?',(int(time.time()),))
        db.execute('INSERT INTO auth_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],int(time.time())+604800))
    response.set_cookie('session',token,httponly=True,samesite='strict',secure=os.getenv('APP_SECURE_COOKIE')=='1',max_age=604800,path='/')
    return user
@app.post('/api/v1/auth/register',status_code=201)
def register(data: Credentials,response: Response):
    email=data.email.strip().lower()
    if '@' not in email or any(c.isspace() for c in email):
        raise HTTPException(422,'邮箱格式不正确')
    with connect() as db:
        cursor=db.execute('INSERT INTO users(email,password_hash) VALUES(?,?)',(email,password_hash(data.password)))
        user={'id':cursor.lastrowid,'email':email,'role':'user'}
        db.execute('INSERT INTO favorite_folders(user_id,name) VALUES(?,?)',(user['id'],'默认收藏'))
    return session_response(response,user)
@app.post('/api/v1/auth/login')
def login(data: Credentials,response: Response):
    with connect() as db:
        row=db.execute('SELECT * FROM users WHERE email=?',(data.email.strip().lower(),)).fetchone()
    if not row or not verify_password(data.password,row['password_hash']):
        raise HTTPException(401,'邮箱或密码错误')
    return session_response(response,{k:row[k] for k in ('id','email','role')})
@app.get('/api/v1/auth/me')
def me(user=Depends(current_user)):
    return user
@app.post('/api/v1/auth/logout')
def logout(request: Request,response: Response,user=Depends(current_user)):
    with connect() as db:
        db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(hashlib.sha256(request.cookies['session'].encode()).hexdigest(),))
    response.delete_cookie('session',path='/')
    return {'ok':True}
@app.get('/api/v1/me/favorites')
def favorites(user=Depends(current_user)):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT r.id,r.name,r.status FROM favorites f JOIN favorite_folders ff ON ff.id=f.folder_id JOIN recipes r ON r.id=f.recipe_id WHERE ff.user_id=? ORDER BY r.id',(user['id'],))]
@app.put('/api/v1/me/favorites/{recipe_id}')
def favorite(recipe_id: int,user=Depends(current_user)):
    with connect() as db:
        published(db,recipe_id)
        db.execute('INSERT OR IGNORE INTO favorite_folders(user_id,name) VALUES(?,?)',(user['id'],'默认收藏'))
        folder=db.execute('SELECT id FROM favorite_folders WHERE user_id=? AND name=?',(user['id'],'默认收藏')).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO favorites VALUES(?,?)',(folder,recipe_id))
    return {'ok':True}
@app.delete('/api/v1/me/favorites/{recipe_id}')
def remove_favorite(recipe_id: int,user=Depends(current_user)):
    with connect() as db:
        db.execute('DELETE FROM favorites WHERE recipe_id=? AND folder_id IN (SELECT id FROM favorite_folders WHERE user_id=?)',(recipe_id,user['id']))
    return {'ok':True}
class ReviewInput(BaseModel):
    rating: int=Field(ge=1,le=5,strict=True)
    body: str=Field(default='',max_length=5000)
@app.put('/api/v1/recipes/{recipe_id}/review')
def review(recipe_id: int,data: ReviewInput,user=Depends(current_user)):
    with connect() as db:
        published(db,recipe_id)
        db.execute('INSERT INTO reviews(user_id,recipe_id,rating,body) VALUES(?,?,?,?) ON CONFLICT(user_id,recipe_id) DO UPDATE SET rating=excluded.rating,body=excluded.body',(user['id'],recipe_id,data.rating,data.body.strip()))
    return {'ok':True}
@app.delete('/api/v1/recipes/{recipe_id}/review')
def delete_review(recipe_id: int,user=Depends(current_user)):
    with connect() as db:
        db.execute('DELETE FROM reviews WHERE user_id=? AND recipe_id=?',(user['id'],recipe_id))
    return {'ok':True}
@app.get('/api/v1/me/reviews')
def my_reviews(user=Depends(current_user)):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT v.*,r.name FROM reviews v JOIN recipes r ON r.id=v.recipe_id WHERE v.user_id=? ORDER BY v.id DESC',(user['id'],))]
@app.post('/api/v1/recipes/{recipe_id}/cooking',status_code=201)
def start_cooking(recipe_id: int,user=Depends(current_user)):
    with connect() as db:
        published(db,recipe_id)
        if not db.execute('SELECT 1 FROM recipe_steps WHERE recipe_id=?',(recipe_id,)).fetchone():
            raise HTTPException(409,'没有可用步骤')
        cursor=db.execute('INSERT INTO cooking_sessions(user_id,recipe_id) VALUES(?,?)',(user['id'],recipe_id))
        return dict(db.execute('SELECT * FROM cooking_sessions WHERE id=?',(cursor.lastrowid,)).fetchone())
@app.get('/api/v1/me/cooking')
def history(user=Depends(current_user)):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT s.*,r.name FROM cooking_sessions s JOIN recipes r ON r.id=s.recipe_id WHERE user_id=? ORDER BY s.id DESC LIMIT 100',(user['id'],))]
class ProgressInput(BaseModel):
    current_step: int=Field(ge=1)
    status: str
    version: int=Field(ge=1)
@app.patch('/api/v1/me/cooking/{session_id}')
def progress(session_id: int,data: ProgressInput,user=Depends(current_user)):
    if data.status not in ('in_progress','paused','completed','cancelled'):
        raise HTTPException(422,'进度状态无效')
    with connect() as db:
        row=db.execute('SELECT * FROM cooking_sessions WHERE id=? AND user_id=?',(session_id,user['id'])).fetchone()
        if not row:
            raise HTTPException(404,'记录不存在')
        count=db.execute('SELECT count(*) FROM recipe_steps WHERE recipe_id=?',(row['recipe_id'],)).fetchone()[0]
        if data.current_step>count:
            raise HTTPException(422,'步骤超出范围')
        if row['status'] in ('completed','cancelled'):
            raise HTTPException(409,'已结束的记录不能修改')
        changed=db.execute('UPDATE cooking_sessions SET current_step=?,status=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND version=?',(data.current_step,data.status,session_id,user['id'],data.version)).rowcount
        if not changed:
            raise HTTPException(409,'进度已变更，请刷新后重试')
        return dict(db.execute('SELECT * FROM cooking_sessions WHERE id=?',(session_id,)).fetchone())
@app.get('/api/v1/admin/imports')
def imports(user=Depends(current_user)):
    if user['role']!='admin':
        raise HTTPException(403,'需要管理员权限')
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM import_runs ORDER BY id DESC')]
class RecipeStatus(BaseModel):
    status: str
@app.patch('/api/v1/admin/recipes/{recipe_id}')
def change_status(recipe_id: int,data: RecipeStatus,user=Depends(current_user)):
    if user['role']!='admin':
        raise HTTPException(403,'需要管理员权限')
    if data.status not in ('draft','published','archived'):
        raise HTTPException(422,'状态无效')
    with connect() as db:
        if not db.execute('UPDATE recipes SET status=? WHERE id=?',(data.status,recipe_id)).rowcount:
            raise HTTPException(404,'食谱不存在')
    return {'ok':True}
from .ingredient_routes import router as ingredient_router
app.include_router(ingredient_router)

app.mount('/',StaticFiles(directory=ROOT/'frontend',html=True),name='frontend')


