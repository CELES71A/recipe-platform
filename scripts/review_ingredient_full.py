"""Read-only full-dataset HTTP review with independent exact-fraction ranking.

Start app.main against the same --db first. Writes only the JSON evidence file.
"""
import argparse
from contextlib import closing
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import time
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.ingredient_matching import ranking_sql, ingredient_pattern

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--db',type=Path,required=True)
parser.add_argument('--base',default='http://127.0.0.1:8770')
parser.add_argument('--output',type=Path,default=Path('data/stage4/full_review.json'))
parser.add_argument('--repetitions',type=int,default=3)
args=parser.parse_args()
if args.repetitions<1: parser.error('--repetitions must be positive')
db_path=args.db.resolve()


def http(path,body=None):
    request=urllib.request.Request(args.base+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','X-Requested-With':'recipe-platform'})
    started=time.perf_counter()
    with urllib.request.urlopen(request,timeout=120) as response:
        assert response.status==200
        value=json.load(response)
    return value,round((time.perf_counter()-started)*1000,3)


def reference(db,ids):
    marks=','.join('?' for _ in ids)
    sql=f"""SELECT ri.recipe_id,COUNT(DISTINCT ri.ingredient_id) AS required_count,
             COUNT(DISTINCT CASE WHEN ri.ingredient_id IN ({marks}) THEN ri.ingredient_id END) AS matched_count
             FROM recipe_ingredients ri JOIN recipes r ON r.id=ri.recipe_id
             WHERE r.status='published' GROUP BY ri.recipe_id HAVING matched_count>0"""
    values=[(rid,required,matched,Fraction(matched,required)) for rid,required,matched in db.execute(sql,ids)]
    values.sort(key=lambda row:(row[1]-row[2],-row[3],row[0]))
    return values


result={'recorded_at':datetime.now().astimezone().isoformat(),'database':db_path.name,
        'entrypoint':'app.main:app','python':sys.version.split()[0],
        'sqlite':sqlite3.sqlite_version,'platform':platform.system()+' '+platform.machine(),
        'timing_note':'sequential local HTTP wall time; no cache reset; three samples are not a P95/load test',
        'lookup':[],'matches':[]}
with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True)) as db:
    db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON')
    result['counts']={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
        ('recipes','ingredients','recipe_ingredients','recipe_steps','source_reviews','quarantined_reviews')}
    result['migrations']=[r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]
    result['statuses']=dict(db.execute('SELECT status,COUNT(*) FROM recipes GROUP BY status'))
    result['integrity_check']=[r[0] for r in db.execute('PRAGMA integrity_check')]
    result['foreign_key_violations']=len(db.execute('PRAGMA foreign_key_check').fetchall())
    assert result['integrity_check']==['ok'] and result['foreign_key_violations']==0
    stats,_=http('/api/v1/stats')
    assert stats=={key:result['counts'][key] for key in ('recipes','ingredients','source_reviews')}
    names=dict(db.execute('SELECT id,name FROM ingredients'))
    frequencies={r[0]:r[1] for r in db.execute("SELECT ri.ingredient_id,COUNT(DISTINCT ri.recipe_id) "
        "FROM recipe_ingredients ri JOIN recipes r ON r.id=ri.recipe_id WHERE r.status='published' GROUP BY ri.ingredient_id")}
    rare=min(frequencies,key=lambda key:(frequencies[key],key))
    result['rare_ingredient']={'id':rare,'name':names[rare],'published_recipe_count':frequencies[rare]}
    for q,page in (('blue',1),('salt',1),(names[rare][:80],1),('%',1),('blue',2)):
        timings=[]
        for _ in range(args.repetitions):
            data,ms=http('/api/v1/ingredients?'+urllib.parse.urlencode({'q':q,'page':page,'page_size':20}));timings.append(ms)
        expected=sorted(({'id':i,'name':name} for i,name in names.items() if q.lower() in name.lower()),key=lambda item:(item['name'].lower(),item['id']))
        assert data['total']==len(expected) and data['items']==expected[(page-1)*20:page*20]
        plans=[dict(r) for r in db.execute("EXPLAIN QUERY PLAN SELECT id,name FROM ingredients WHERE name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?",(ingredient_pattern(q),20,(page-1)*20))]
        result['lookup'].append({'q':q,'page':page,'total':data['total'],'elapsed_ms':timings,'median_ms':statistics.median(timings),'query_plan':plans})
    cases=[('common_salt',[21],1),('common_sugar',[30],1),('rare',[rare],1),
           ('multiple',[9,21,30],1),('multiple_page2',[9,21,30],2),
           ('recipe38_full',[1,2,3,4],1),('recipe38_partial',[1,2],None)]
    previous_ids=None; expected=[]
    for label,ids,page in cases:
        started=time.perf_counter()
        if ids!=previous_ids: expected=reference(db,ids);previous_ids=ids
        reference_ms=round((time.perf_counter()-started)*1000,3)
        if page is None: page=next(i for i,row in enumerate(expected) if row[0]==38)//12+1
        body={'ingredient_ids':ids,'page':page,'page_size':12};timings=[]
        for _ in range(args.repetitions):
            data,ms=http('/api/v1/recipe-matches',body);timings.append(ms)
        wanted=expected[(page-1)*12:page*12]
        assert data['total']==len(expected) and data['ingredient_ids']==sorted(set(ids))
        assert [r['id'] for r in data['items']]==[r[0] for r in wanted]
        for item,(_,required,matched,ratio) in zip(data['items'],wanted):
            rows=db.execute('SELECT DISTINCT i.id,i.name FROM recipe_ingredients ri JOIN ingredients i '
                            'ON i.id=ri.ingredient_id WHERE ri.recipe_id=? ORDER BY i.id',(item['id'],)).fetchall()
            needed=[dict(r) for r in rows]
            assert item['matched_ingredients']==[r for r in needed if r['id'] in ids]
            assert item['missing_ingredients']==[r for r in needed if r['id'] not in ids]
            assert (item['required_count'],item['matched_count'],item['missing_count'])==(required,matched,required-matched)
            assert math.isclose(item['coverage'],float(ratio),rel_tol=1e-15,abs_tol=1e-15)
        plans=[dict(r) for r in db.execute('EXPLAIN QUERY PLAN '+ranking_sql(ids),ids+[12,(page-1)*12])]
        record={'case':label,'input':body,'ingredient_names':[names[i] for i in ids],
                'total':data['total'],'elapsed_ms':timings,'median_ms':statistics.median(timings),
                'independent_reference_ms':reference_ms,'page_recipe_ids':[r['id'] for r in data['items']],
                'all_candidate_order_sha256':hashlib.sha256(json.dumps([r[0] for r in expected]).encode()).hexdigest(),
                'independent_order_counts_details_verified':True,'query_plan':plans,
                'first_item':data['items'][0] if data['items'] else None}
        if label=='recipe38_full':
            duplicate,_=http('/api/v1/recipe-matches',{'ingredient_ids':[1,1,2,2,3,4],'page_size':12})
            assert duplicate==data
            record['duplicate_input_identical']=True
            complete=[{'id':r['id'],'required_count':r['required_count']} for r in data['items'] if r['missing_count']==0]
            assert [r['id'] for r in complete]==sorted(r['id'] for r in complete)
            assert len({r['required_count'] for r in complete})>1
            record['different_size_full_coverage_id_order']=complete
        if label.startswith('recipe38_'):
            target=next(r for r in data['items'] if r['id']==38)
            assert target['coverage']==(1.0 if label.endswith('full') else 0.5)
            record['recipe38']=target
        result['matches'].append(record)
        print(label,'total',data['total'],'median_ms',record['median_ms'],flush=True)
    detail,_=http('/api/v1/recipes/38')
    assert detail['name']=='Low-Fat Berry Blue Frozen Dessert' and len(detail['ingredients'])==4 and len(detail['steps'])==9
    result['recipe38_detail_verified']=True
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('PASS: full review',args.output)
