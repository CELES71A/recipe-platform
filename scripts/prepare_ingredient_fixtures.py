"""Rebuild the 100-recipe ingredient fixtures from externally supplied CSV files.

Run from the repository root. Existing databases/expectations are never overwritten.
Uses the unchanged importer, then independently calculates expected sets/fractions.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--recipes', type=Path, required=True)
parser.add_argument('--reviews', type=Path, required=True)
parser.add_argument('--sqlite', default='sqlite3')
args = parser.parse_args()
sample = ROOT/'data/stage1_sample.db'
no_match = ROOT/'data/stage1_no_match.db'
output = ROOT/'data/stage1/acceptance_expected.json'
for file in (sample,no_match,output):
    if file.exists():
        parser.error(f'{file} already exists; use a fresh checkout or preserve it before rebuilding')
for file in (args.recipes,args.reviews):
    if not file.is_file(): parser.error(f'CSV not found: {file}')
subprocess.run([sys.executable,'-B',str(ROOT/'scripts/import_data.py'),
                '--sqlite',args.sqlite,'--recipes',str(args.recipes.resolve()),
                '--reviews',str(args.reviews.resolve()),'--db',str(sample),'--limit','100'],
               cwd=ROOT,check=True)


def readonly(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def expected(db, ids):
    names=dict(db.execute('SELECT id,name FROM ingredients'))
    selected=set(ids)
    assert selected <= names.keys()
    required=defaultdict(set); titles={}
    for row in db.execute("SELECT r.id,r.name,ri.ingredient_id FROM recipes r "
                          "JOIN recipe_ingredients ri ON ri.recipe_id=r.id WHERE r.status='published'"):
        required[row[0]].add(row[2]); titles[row[0]]=row[1]
    items=[]
    for rid,needed in required.items():
        matched=needed & selected
        if not matched: continue
        ratio=Fraction(len(matched),len(needed))
        items.append({'id':rid,'name':titles[rid], 'required_count':len(needed),
                      'matched_count':len(matched),'missing_count':len(needed-matched),
                      'coverage':float(ratio),'coverage_fraction':str(ratio),
                      'matched_ingredients':[{'id':i,'name':names[i]} for i in sorted(matched)],
                      'missing_ingredients':[{'id':i,'name':names[i]} for i in sorted(needed-selected)]})
    items.sort(key=lambda item:(item['missing_count'],-Fraction(item['coverage_fraction']),item['id']))
    return {'input_ids':ids,'distinct_input_ids':sorted(selected),'expected_http_status':200,
            'total':len(items),'ordered_recipe_ids':[item['id'] for item in items],
            'items':items,'api_status':'independent expected fixture, not an API execution'}


inputs={
    'full_38':[1,2,3,4], 'partial_38':[1,2], 'duplicate_input':[1,1,2,2,3,4],
    'extra_selected':[1,2,3,4,21], 'duplicate_in_recipe_43':[30,30],
    'full_43_distinct':[6,21,30,51,52,53,54,55,56,57,58],
    'fraction_52':[30,90], 'sorting':[21,30],
}
with closing(readonly(sample)) as db:
    counts={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
            for t in ('recipes','ingredients','recipe_ingredients','recipe_steps','source_reviews')}
    assert counts=={'recipes':100,'ingredients':335,'recipe_ingredients':900,
                    'recipe_steps':872,'source_reviews':815}, ('unexpected dataset',counts)
    assert db.execute('SELECT name FROM recipes WHERE id=38').fetchone()[0]=='Low-Fat Berry Blue Frozen Dessert'
    cases={name:expected(db,ids) for name,ids in inputs.items()}
    assert cases['full_38']['total']==15 and cases['partial_38']['total']==8
    assert cases['sorting']['total']==60
    assert cases['duplicate_input']['items']==cases['full_38']['items']
    assert [r[0] for r in db.execute('SELECT DISTINCT recipe_id FROM recipe_ingredients WHERE ingredient_id=3')]==[38]
    lookup=[dict(row) for row in db.execute("SELECT id,name FROM ingredients WHERE name LIKE ? ORDER BY name COLLATE NOCASE,id",('%blue%',))]
    with closing(sqlite3.connect(no_match)) as dest: db.backup(dest)
with closing(sqlite3.connect(no_match)) as db:
    with db:
        assert db.execute("UPDATE recipes SET status='archived' WHERE id=38 AND status='published'").rowcount==1
with closing(readonly(no_match)) as db:
    cases['no_match']=expected(db,[3]); assert cases['no_match']['total']==0
for path in (sample,no_match):
    with closing(readonly(path)) as db:
        assert [r[0] for r in db.execute('PRAGMA integrity_check')]==['ok']
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
cases['empty_input']={'input_ids':[],'expected_http_status':422,'expected_code':'HTTP_422','expected_message':'请至少选择一种食材'}
cases['unknown_input']={'input_ids':[1,999999],'expected_http_status':422,'expected_code':'HTTP_422','expected_message':'未知食材 ID：999999'}
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps({'fixture_version':1,'counts':counts,'ingredient_lookup_blue':lookup,
                              'cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('PASS: regenerated sample, no-match database and independent expected JSON')
print('Fixtures remain local under data/; no raw data or database is committed.')
