"""HTTP smoke tests against a running local server; use a disposable test DB."""
import http.cookiejar
import json
import time
import urllib.request
import urllib.error
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--base',default='http://127.0.0.1:8767')
args=parser.parse_args()
base=args.base
jar=http.cookiejar.CookieJar()
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
results=[]
def call(method,path,data=None,expected=200,opener=client):
    request=urllib.request.Request(base+'/api/v1'+path,data=json.dumps(data).encode() if data is not None else (b'' if method in ('POST','PUT','PATCH') else None),method=method,headers={'Content-Type':'application/json','X-Requested-With':'recipe-platform'})
    try:
        response=opener.open(request,timeout=30)
    except urllib.error.HTTPError as error:
        response=error
    content=json.loads(response.read())
    assert response.status==expected,(method,path,response.status,content)
    results.append(f'{method} {path} -> {expected}')
    return content
call('GET','/health')
recipes=call('GET','/recipes?q=chicken&max_minutes=30')
call('GET','/recipes?q=%22%20OR%20%28')
call('GET','/recipes?page=0',expected=422)
first=call('GET','/recipes')['items'][0]['id']
detail=call('GET',f'/recipes/{first}')
assert detail['steps'] and detail['ingredients']
call('PUT',f'/me/favorites/{first}',expected=401)
credentials={'email':f'test{time.time_ns()}@example.com','password':'Testing-123!'}
call('POST','/auth/register',credentials,201)
call('POST','/auth/register',credentials,409)
call('GET','/auth/me')
call('PUT',f'/me/favorites/{first}')
call('PUT',f'/me/favorites/{first}')
assert len(call('GET','/me/favorites'))==1
call('PUT',f'/recipes/{first}/review',{'rating':0,'body':''},422)
call('PUT',f'/recipes/{first}/review',{'rating':5,'body':'Smoke test'})
call('PUT',f'/recipes/{first}/review',{'rating':4,'body':'Updated'})
assert len(call('GET','/me/reviews'))==1
session=call('POST',f'/recipes/{first}/cooking',expected=201)
session=call('PATCH',f'/me/cooking/{session["id"]}',{'current_step':1,'status':'paused','version':session['version']})
call('PATCH',f'/me/cooking/{session["id"]}',{'current_step':1,'status':'in_progress','version':1},409)
call('GET','/admin/imports',expected=403)
other=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
call('POST','/auth/register',{'email':f'other{time.time_ns()}@example.com','password':'Testing-123!'},201,other)
assert call('GET','/me/favorites',opener=other)==[]
call('PATCH',f'/me/cooking/{session["id"]}',{'current_step':1,'status':'completed','version':session['version']},404,other)
session=call('PATCH',f'/me/cooking/{session["id"]}',{'current_step':1,'status':'completed','version':session['version']})
call('PATCH',f'/me/cooking/{session["id"]}',{'current_step':1,'status':'in_progress','version':session['version']},409)
call('DELETE',f'/recipes/{first}/review')
call('DELETE',f'/me/favorites/{first}')
call('POST','/auth/logout')
call('GET','/auth/me',expected=401)
call('POST','/auth/login',{**credentials,'password':'Incorrect-123'},401)
call('POST','/auth/login',credentials)
assert call('GET','/me/cooking')[0]['status']=='completed'
print('\n'.join(results))
print(f'PASS: {len(results)} HTTP checks')
