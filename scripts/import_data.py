"""Normalize CSV then import using the independent sqlite3 command-line program."""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MISSING={'','NA','NaN','NULL','None','c()'}

def clean(value):
    return None if value is None or value.strip() in MISSING else value.strip()

def vector(value):
    value=clean(value)
    if not value:
        return []
    if not value.startswith('c('):
        if value.startswith('"') and value.endswith('"'):
            return [decode(value[1:-1])]
        return [value]
    # R string tokens, including escaped quotes and explicit NA quantities.
    result=[]
    for match in re.finditer(r'"((?:[^"\\]|\\.)*)"|\bNA\b',value[2:-1],re.S):
        result.append(decode(match.group(1)) if match.group(1) is not None else '')
    if not result and value[2:-1].strip():
        raise ValueError('Unrecognized R vector')
    return result

def decode(value):
    return re.sub(r'\\([\\"nrt])',lambda m:{'n':'\n','r':'\r','t':'\t'}.get(m[1],m[1]),value)

def seconds(value):
    value=clean(value)
    if not value:
        return None
    match=re.fullmatch(r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?',value)
    if not match:
        return None
    return int(sum(float(n or 0)*factor for n,factor in zip(match.groups(),(86400,3600,60,1))))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite',default=os.getenv('SQLITE_CLI','sqlite3'))
    parser.add_argument('--recipes',type=Path,default=ROOT/'data/raw/recipes.csv')
    parser.add_argument('--reviews',type=Path,default=ROOT/'data/raw/reviews.csv')
    parser.add_argument('--db',type=Path,default=Path(os.getenv('APP_DB_PATH',str(ROOT/'data/recipe_platform.db'))))
    parser.add_argument('--load-prepared',action='store_true',help='Retry generated data/prepared/import.sql without parsing source CSV again')
    parser.add_argument('--limit',type=int,default=0,help='0 = all recipes; include only their reviews')
    args=parser.parse_args()
    if args.limit<0:
        parser.error('--limit must be nonnegative')
    cli=shutil.which(args.sqlite)
    if not cli:
        parser.error('sqlite3 CLI not found. Install SQLite Tools or supply --sqlite path/to/sqlite3.exe')
    if not args.recipes.is_file() or not args.reviews.is_file():
        parser.error('Place recipes.csv and reviews.csv in data/raw or supply their paths')
    db=args.db if args.db.is_absolute() else ROOT/args.db
    db.parent.mkdir(parents=True,exist_ok=True)
    def run(sql,capture=False):
        result=subprocess.run([cli,'-batch','-bail',str(db)],input=sql,encoding='utf-8',stdout=subprocess.PIPE if capture else None,check=True)
        return result.stdout if capture else None
    if args.load_prepared:
        retry_started=time.time()
        prepared=ROOT/'data/prepared'
        run((prepared/'import.sql').read_text(encoding='utf-8-sig'))
        checks=run('PRAGMA integrity_check; PRAGMA foreign_key_check;',True).strip()
        if checks!='ok':
            raise RuntimeError(checks)
        report=json.loads(run('SELECT report_json FROM import_runs ORDER BY id DESC LIMIT 1;',True))
        if 'elapsed_seconds' in report:
            report['preparation_seconds']=report.pop('elapsed_seconds')
        report['retry_seconds']=round(time.time()-retry_started,1)
        report.update(integrity_check='ok',foreign_key_violations=0)
        report['database_counts']={table:int(run(f'SELECT count(*) FROM {table};',True)) for table in ('recipes','recipe_steps','recipe_ingredients','ingredients','source_reviews','quarantined_reviews')}
        (prepared/'import_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return
    schema=run("SELECT name FROM sqlite_master WHERE name='schema_migrations';",True).strip()
    versions=set(run('SELECT version FROM schema_migrations;',True).split()) if schema else set()
    for migration in sorted((ROOT/'backend/migrations').glob('*.sql')):
        if str(int(migration.name.split('_')[0])) not in versions:
            run('PRAGMA foreign_keys=ON;\nBEGIN IMMEDIATE;\n'+migration.read_text(encoding='utf-8-sig')+'\nCOMMIT;')
    run("INSERT INTO import_runs(status) VALUES('running');")
    run_id=run('SELECT max(id) FROM import_runs;',True).strip()
    output=ROOT/'data/prepared'
    output.mkdir(parents=True,exist_ok=True)
    tables={
        'categories':['id','name'],
        'recipes':['id','name','description','category_id','status','total_seconds','image_url','source_rating','source_review_count','nutrition_json','servings'],
        'ingredients':['id','name'],
        'recipe_steps':['recipe_id','position','instruction'],
        'recipe_ingredients':['recipe_id','position','ingredient_id','quantity'],
        'source_recipe_records':['recipe_id','raw_json'],
        'source_reviews':['id','recipe_id','source_author_id','raw_rating','rating','body','raw_json'],
        'quarantined_reviews':['id','reason','raw_json'],
    }
    counts={name:0 for name in tables}
    category_map={row[1]:int(row[0]) for row in csv.reader(run('SELECT id,name FROM categories;',True).splitlines(),delimiter='|') if len(row)==2}
    ingredient_map={row[1]:int(row[0]) for row in csv.reader(run('SELECT id,name FROM ingredients;',True).splitlines(),delimiter='|') if len(row)==2}
    next_ids={'categories':max(category_map.values(),default=0),'ingredients':max(ingredient_map.values(),default=0)}
    ids=set()
    csv.field_size_limit(16*1024*1024)
    report={'scope':'all' if not args.limit else f'first {args.limit} recipes','zero_ratings':0,'unselected_reviews':0,'recipe_errors':0,'quantity_mismatches':0}
    started=time.time()
    try:
        with ExitStack() as stack:
            writers={name:csv.writer(stack.enter_context((output/f'{name}.csv').open('w',encoding='utf-8',newline=''))) for name in tables}
            errors=stack.enter_context((output/'errors.jsonl').open('w',encoding='utf-8'))
            def emit(table,row):
                writers[table].writerow(row); counts[table]+=1
            def lookup(mapping,table,name):
                if name not in mapping:
                    next_ids[table]+=1
                    mapping[name]=next_ids[table]
                    emit(table,[mapping[name],name])
                return mapping[name]
            with args.recipes.open(encoding='utf-8-sig',newline='') as source:
                for number,row in enumerate(csv.DictReader(source),1):
                    if args.limit and number>args.limit:
                        break
                    try:
                        recipe_id=int(row['RecipeId'])
                        parts=vector(row['RecipeIngredientParts'])
                        quantities=vector(row['RecipeIngredientQuantities'])
                        steps=vector(row['RecipeInstructions'])
                        images=vector(row['Images'])
                        if len(parts)!=len(quantities):
                            report['quantity_mismatches']+=1
                        category=clean(row['RecipeCategory'])
                        category_id=lookup(category_map,'categories',category) if category else None
                        nutrition={key:clean(row[key]) for key in ('Calories','FatContent','SaturatedFatContent','CholesterolContent','SodiumContent','CarbohydrateContent','FiberContent','SugarContent','ProteinContent')}
                        emit('recipes',[recipe_id,row['Name'],clean(row['Description']),category_id,'published',seconds(row['TotalTime']),next((u for u in images if u.startswith(('https://','http://'))),None),clean(row['AggregatedRating']),clean(row['ReviewCount']),json.dumps(nutrition),clean(row['RecipeServings'])])
                        emit('source_recipe_records',[recipe_id,json.dumps(row,ensure_ascii=False)])
                        for position,instruction in enumerate((s for s in steps if s.strip()),1):
                            emit('recipe_steps',[recipe_id,position,instruction])
                        for position,name in enumerate(parts,1):
                            if name.strip():
                                ingredient_id=lookup(ingredient_map,'ingredients',' '.join(name.lower().split()))
                                emit('recipe_ingredients',[recipe_id,position,ingredient_id,quantities[position-1] if len(parts)==len(quantities) else None])
                        ids.add(recipe_id)
                    except (ValueError,KeyError) as error:
                        report['recipe_errors']+=1
                        errors.write(json.dumps({'recipe_id':row.get('RecipeId'),'error':str(error),'raw':row},ensure_ascii=False)+'\n')
                    if number%25000==0:
                        print(f'Prepared {number:,} recipes',flush=True)
            with args.reviews.open(encoding='utf-8-sig',newline='') as source:
                for number,row in enumerate(csv.DictReader(source),1):
                    recipe_id=int(row['RecipeId'])
                    if recipe_id not in ids:
                        if args.limit:
                            report['unselected_reviews']+=1
                        else:
                            emit('quarantined_reviews',[row['ReviewId'],'missing_recipe',json.dumps(row,ensure_ascii=False)])
                        continue
                    raw=int(float(row['Rating'])) if clean(row['Rating']) else None
                    if raw==0:
                        report['zero_ratings']+=1
                    emit('source_reviews',[row['ReviewId'],recipe_id,row['AuthorId'],raw,raw if raw and 1<=raw<=5 else None,clean(row['Review']),json.dumps(row,ensure_ascii=False)])
                    if number%100000==0:
                        print(f'Prepared {number:,} reviews',flush=True)
        commands=['.bail on','.timeout 15000','PRAGMA foreign_keys=ON;','PRAGMA journal_mode=WAL;','PRAGMA temp_store=FILE;','BEGIN IMMEDIATE;']
        # CSV is imported into TEXT staging tables, then cast and merged into constrained relations.
        for table,columns in tables.items():
            commands.append(f'.print Importing {table}...')
            stage='stage_'+table
            commands.append(f'CREATE TEMP TABLE {stage}('+','.join(f'"{column}" TEXT' for column in columns)+');')
            commands.append(f'.import --csv "{(output/(table+".csv")).as_posix()}" {stage}')
            if table=='recipe_steps':
                commands.append('DELETE FROM recipe_steps WHERE recipe_id IN (SELECT id FROM stage_recipes);')
            if table=='recipe_ingredients':
                commands.append('DELETE FROM recipe_ingredients WHERE recipe_id IN (SELECT id FROM stage_recipes);')
            select=','.join(f'NULLIF("{column}",\'\')' for column in columns)
            keys={'recipe_steps':['recipe_id','position'],'recipe_ingredients':['recipe_id','position'],'source_recipe_records':['recipe_id']}.get(table,['id'])
            updates=','.join(f'{c}=excluded.{c}' for c in columns if c not in keys and not (table=='recipes' and c=='status'))
            commands.append(f'INSERT INTO {table}('+','.join(columns)+f') SELECT {select} FROM {stage} WHERE true ON CONFLICT('+','.join(keys)+f') DO UPDATE SET {updates};')
        report.update(counts)
        report['preparation_seconds']=round(time.time()-started,1)
        payload=json.dumps(report,ensure_ascii=False).replace("'","''")
        commands.extend([f"UPDATE import_runs SET status='succeeded',report_json='{payload}' WHERE id={run_id};",'COMMIT;','ANALYZE;','PRAGMA wal_checkpoint(TRUNCATE);'])
        script='\n'.join(commands)+'\n'
        (output/'import.sql').write_text(script,encoding='utf-8')
        print('Importing normalized CSV via SQLite CLI; this may take several minutes...',flush=True)
        run(script)
        checks=run('PRAGMA integrity_check; PRAGMA foreign_key_check;',True).strip()
        if checks!='ok':
            raise RuntimeError('Database checks failed: '+checks)
        report['integrity_check']='ok'; report['foreign_key_violations']=0
        report['database_counts']={table:int(run(f'SELECT count(*) FROM {table};',True)) for table in ('recipes','recipe_steps','recipe_ingredients','ingredients','source_reviews','quarantined_reviews')}
        report['elapsed_seconds']=round(time.time()-started,1)
        (output/'import_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    except Exception:
        run(f"UPDATE import_runs SET status='failed' WHERE id={run_id};")
        raise
if __name__=='__main__':
    main()





