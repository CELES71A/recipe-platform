"""Real HTTP acceptance against disposable copies of the Stage 1 databases.

Run: .venv/bin/python -B scripts/test_ingredient_api.py
No third-party test client is required. Artifacts remain under data/stage2/.
"""
import atexit
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.ingredient_matching import details_sql, ingredient_pattern, matched_cte, match_recipes, ranking_sql
from app.main import app as production_app

MAX_INT = 2**63 - 1
ARTIFACTS = ROOT / 'data/stage2'
SERVERS = []
HTTP_RECORDS = []
EXAMPLES = {}
REGRESSIONS = {}
NEW_PATHS = {'/api/v1/ingredients', '/api/v1/recipe-matches'}
# FastAPI may retain included routers as lazy route objects without .path.
registered = NEW_PATHS.intersection(production_app.openapi()['paths'])
if registered and registered != NEW_PATHS:
    raise RuntimeError('Only part of the router is registered; review main.py')
ENTRYPOINT = 'app.main:app' if registered == NEW_PATHS else 'ingredient_test_app:app'
if not registered:
    # The temporary entry point will add the router after this inspection.
    production_app.openapi_schema = None


def stop_servers():
    for process, log in SERVERS:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()


atexit.register(stop_servers)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(source, target):
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


def start_server(database, label):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (RUN / f'{label}_server.log').open('w')
    env = dict(os.environ, APP_DB_PATH=str(database), PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join((str(ROOT/'backend'), str(ROOT/'scripts'))))
    command = [sys.executable, '-B', '-m', 'uvicorn', ENTRYPOINT,
               '--host', '127.0.0.1', '--port', str(port), '--no-access-log']
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    SERVERS.append((process, log))
    base = f'http://127.0.0.1:{port}'
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError((RUN/f'{label}_server.log').read_text())
        try:
            with urllib.request.urlopen(base+'/api/v1/health', timeout=1):
                return base
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
    raise RuntimeError(f'{label} server did not start')


def request(base, path, body=None, status=200, headers=None, raw=None):
    actual_headers = {'Content-Type': 'application/json', 'X-Requested-With': 'recipe-platform'}
    if headers is not None:
        actual_headers = headers
    payload = json.dumps(body).encode() if body is not None else raw
    req = urllib.request.Request(base+path, data=payload,
                                 method='POST' if payload is not None else 'GET', headers=actual_headers)
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = json.load(response)
        actual_status = response.status
    HTTP_RECORDS.append({'path': path, 'body': body, 'expected_status': status,
                         'actual_status': actual_status})
    assert actual_status == status, (path, body, actual_status, data)
    if status >= 400:
        assert set(data) == {'code', 'message', 'request_id'}, data
        assert isinstance(data['request_id'], str) and len(data['request_id']) == 32, data
    return data


class IngredientAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads((ROOT/'data/stage1/acceptance_expected.json').read_text())
        cls.original_hashes = {name: digest(ROOT/'data'/name) for name in
                               ('stage1_sample.db', 'stage1_no_match.db')}
        cls.base_db, cls.no_db, cls.edge_db = [RUN/name for name in ('baseline.db', 'no_match.db', 'edges.db')]
        backup(ROOT/'data/stage1_sample.db', cls.base_db)
        backup(ROOT/'data/stage1_no_match.db', cls.no_db)
        backup(ROOT/'data/stage1_sample.db', cls.edge_db)
        with closing(sqlite3.connect(cls.edge_db)) as db:
            with db:
                db.execute('PRAGMA foreign_keys=ON')
                db.executemany('INSERT INTO ingredients(id,name) VALUES(?,?)', [
                    (10000, 'percent% spice'), (10001, 'under_score'),
                    (10002, 'back\\slash'), (10003, 'case tie'), (10004, 'CASE TIE'),
                    (10005, 'a\\%_b'), (MAX_INT, 'int64 boundary')])
                db.executemany('INSERT INTO recipes(id,name,status) VALUES(?,?,?)', [
                    (9100, 'complete one', 'published'), (9101, 'complete two', 'published'),
                    (9102, 'excluded archived', 'archived'), (9103, 'excluded draft', 'draft')])
                db.executemany('INSERT INTO recipe_ingredients(recipe_id,position,ingredient_id) VALUES(?,?,?)', [
                    (9100,1,10000), (9101,1,10000), (9101,2,10001), (9101,3,10000),
                    (9102,1,10000), (9103,1,10000)])
        cls.base = start_server(cls.base_db, 'baseline')
        cls.no_match = start_server(cls.no_db, 'no_match')
        cls.edge = start_server(cls.edge_db, 'edges')

    @classmethod
    def tearDownClass(cls):
        stop_servers()
        assert cls.original_hashes == {name: digest(ROOT/'data'/name) for name in cls.original_hashes}
        snapshots = {}
        for path in (cls.base_db, cls.no_db, cls.edge_db):
            with closing(sqlite3.connect(path)) as db:
                assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
                assert db.execute('PRAGMA foreign_key_check').fetchall() == []
                snapshots[path.name] = dict(db.execute('SELECT status,count(*) FROM recipes GROUP BY status'))
        (RUN/'database_checks.json').write_text(json.dumps(snapshots, indent=2)+'\n')

    def compare(self, response, expected, page=1, page_size=100):
        self.assertEqual(response['ingredient_ids'], expected['distinct_input_ids'])
        self.assertEqual((response['total'],response['page'],response['page_size']),
                         (expected['total'],page,page_size))
        wanted = expected['items'][(page-1)*page_size:page*page_size]
        self.assertEqual(len(response['items']), len(wanted))
        for item, reference in zip(response['items'], wanted):
            for key in ('id','name','required_count','matched_count','missing_count',
                        'matched_ingredients','missing_ingredients'):
                self.assertEqual(item[key], reference[key], (item['id'],key))
            self.assertTrue(math.isclose(item['coverage'],reference['coverage'],rel_tol=1e-15,abs_tol=1e-15))
            self.assertEqual(item['required_count'], item['matched_count']+item['missing_count'])

    def test_01_all_stage1_success_cases(self):
        for name, case in self.expected['cases'].items():
            if case['expected_http_status'] != 200:
                continue
            with self.subTest(case=name):
                base = self.no_match if name == 'no_match' else self.base
                data = request(base, '/api/v1/recipe-matches',
                               {'ingredient_ids':case['input_ids'],'page_size':100})
                self.compare(data, case)
                EXAMPLES[name] = {'total':data['total'],'ordered_recipe_ids':[r['id'] for r in data['items']]}
        EXAMPLES['partial_response'] = request(self.base, '/api/v1/recipe-matches',
                                             {'ingredient_ids':[1,2],'page_size':1})

    def test_02_sorting_cross_page_and_beyond(self):
        expected = self.expected['cases']['sorting']
        observed = []
        for page in range(1, 10):
            data = request(self.base, '/api/v1/recipe-matches',
                           {'ingredient_ids':[21,30],'page':page,'page_size':7})
            self.compare(data, expected, page, 7)
            observed.extend(r['id'] for r in data['items'])
        self.assertEqual(observed,expected['ordered_recipe_ids'])
        data = request(self.base, '/api/v1/recipe-matches',
                       {'ingredient_ids':[21,30],'page':10,'page_size':7})
        self.compare(data,expected,10,7)

    def test_03_complete_recipes_with_different_sizes(self):
        data = request(self.base, '/api/v1/recipe-matches',
                       {'ingredient_ids':[6,21,29,30,90,91,180,181],'page_size':100})
        complete = [r for r in data['items'] if r['missing_count']==0]
        self.assertEqual([r['id'] for r in complete],sorted(r['id'] for r in complete))
        by_id = {r['id']:r for r in complete}
        self.assertEqual(by_id[52]['required_count'],3)
        self.assertEqual(by_id[80]['required_count'],6)
        self.assertEqual(by_id[52]['coverage'],1.0)
        self.assertEqual(by_id[80]['coverage'],1.0)
        EXAMPLES['different_sizes_complete'] = complete
        for page, rid, count in ((1,9100,1),(2,9101,2)):
            data=request(self.edge,'/api/v1/recipe-matches',
                         {'ingredient_ids':[10000,10001],'page':page,'page_size':1})
            self.assertEqual(data['total'],2)
            self.assertEqual(data['items'][0]['id'],rid)
            self.assertEqual(data['items'][0]['required_count'],count)
            self.assertEqual(data['items'][0]['coverage'],1.0)

    def test_04_empty_unknown_and_no_match(self):
        for ids, message in (([],'请至少选择一种食材'),([999999],'未知食材 ID：999999'),
                             ([1,999999],'未知食材 ID：999999'),
                             ([999999,999998,999999],'未知食材 ID：999998, 999999')):
            data=request(self.base,'/api/v1/recipe-matches',{'ingredient_ids':ids},422)
            self.assertEqual((data['code'],data['message']),('HTTP_422',message))
            EXAMPLES['error_'+str(ids)] = data
        data=request(self.no_match,'/api/v1/recipe-matches',{'ingredient_ids':[3]})
        self.assertEqual(data,{'ingredient_ids':[3],'items':[],'total':0,'page':1,'page_size':24})
        lookup=request(self.no_match,'/api/v1/ingredients?q=vanilla%20yogurt')
        self.assertIn({'id':3,'name':'vanilla yogurt'},lookup['items'])

    def test_05_invalid_match_input(self):
        bodies=[{}, {'ingredient_ids':None}, {'ingredient_ids':'1'},
                {'ingredient_ids':[1]*101}, {'ingredient_ids':[1],'extra':1}]
        for value in (True,False,0,-1,1.0,1.5,'1',None,MAX_INT+1,10**100):
            bodies.append({'ingredient_ids':[value]})
        for key in ('page','page_size'):
            for value in (True,False,0,-1,1.0,1.5,'1',None,10**100):
                bodies.append({'ingredient_ids':[1],key:value})
        bodies.append({'ingredient_ids':[1],'page_size':101})
        for body in bodies:
            with self.subTest(body=body):
                data=request(self.base,'/api/v1/recipe-matches',body,422)
                self.assertEqual(data['code'],'VALIDATION_ERROR')
        for raw in (b'{',b'[]',b''):
            data=request(self.base,'/api/v1/recipe-matches',status=422,raw=raw)
            self.assertEqual(data['code'],'VALIDATION_ERROR')
        self.assertEqual(request(self.base,'/api/v1/recipe-matches',{'ingredient_ids':[1]*100})['ingredient_ids'],[1])

    def test_06_int64_and_offset_boundaries(self):
        for path,body in (('/api/v1/recipe-matches',{'ingredient_ids':[1],'page':MAX_INT,'page_size':2}),
                          ('/api/v1/ingredients?page='+str(MAX_INT)+'&page_size=2',None)):
            data=request(self.base,path,body,422)
            self.assertEqual(data['message'],'分页偏移超出 SQLite 整数范围')
        for page,size in ((MAX_INT,1),(MAX_INT//7+1,7)):
            data=request(self.base,'/api/v1/recipe-matches',{'ingredient_ids':[1],'page':page,'page_size':size})
            self.assertEqual(data['items'],[])
            self.assertGreater(data['total'],0)
            self.assertEqual(request(self.base,f'/api/v1/ingredients?page={page}&page_size={size}')['items'],[])
        data=request(self.base,'/api/v1/recipe-matches',{'ingredient_ids':[MAX_INT]},422)
        self.assertEqual(data['code'],'HTTP_422')
        self.assertEqual(request(self.edge,'/api/v1/recipe-matches',{'ingredient_ids':[MAX_INT]})['total'],0)

    def test_07_ingredient_search_and_pagination(self):
        data=request(self.base,'/api/v1/ingredients?q=blue')
        self.assertEqual(data['items'],self.expected['ingredient_lookup_blue'])
        self.assertEqual(data['total'],4)
        EXAMPLES['ingredient_response']=data
        normalized=request(self.base,'/api/v1/ingredients?'+urllib.parse.urlencode({'q':'  BLUE  '}))
        self.assertEqual(data,normalized)
        with closing(sqlite3.connect(self.base_db)) as db:
            expected=[{'id':r[0],'name':r[1]} for r in db.execute('SELECT id,name FROM ingredients ORDER BY name COLLATE NOCASE,id')]
        observed=[]
        for page in range(1,21):
            data=request(self.base,f'/api/v1/ingredients?page={page}&page_size=17')
            self.assertEqual(data['total'],335)
            observed.extend(data['items'])
        self.assertEqual(observed,expected)
        self.assertEqual(request(self.base,'/api/v1/ingredients?page=21&page_size=17')['items'],[])
        self.assertEqual(request(self.base,'/api/v1/ingredients?q=not-in-this-sample')['total'],0)

    def test_08_literal_wildcards_and_name_ties(self):
        for q in ('%', '_', '\\', '\\%_', "' OR 1=1 --", '  CASE   TIE  '):
            with self.subTest(q=q):
                data=request(self.edge,'/api/v1/ingredients?'+urllib.parse.urlencode({'q':q,'page_size':100}))
                normalized=' '.join(q.lower().split())
                with closing(sqlite3.connect(self.edge_db)) as db:
                    expected=[{'id':r[0],'name':r[1]} for r in db.execute('SELECT id,name FROM ingredients ORDER BY name COLLATE NOCASE,id') if normalized in r[1].lower()]
                self.assertEqual(data['items'],expected)
                self.assertEqual(data['total'],len(expected))
        self.assertEqual([r['id'] for r in data['items']],[10003,10004])

    def test_09_invalid_lookup(self):
        for params in ({'q':'x'*101},{'page':0},{'page':-1},{'page':'true'},
                       {'page':'1.5'},{'page':MAX_INT+1},{'page_size':0},
                       {'page_size':101},{'page_size':'true'},{'page':10**100}):
            with self.subTest(params=params):
                data=request(self.base,'/api/v1/ingredients?'+urllib.parse.urlencode(params),status=422)
                self.assertEqual(data['code'],'VALIDATION_ERROR')

    def test_10_existing_request_headers_and_openapi(self):
        for headers in ({'Content-Type':'application/json'},
                        {'Content-Type':'application/json','X-Requested-With':'recipe-platform','Origin':'https://invalid.example'}):
            data=request(self.base,'/api/v1/recipe-matches',{'ingredient_ids':[1]},403,headers)
            self.assertEqual(data['code'],'CSRF_REJECTED')
        request(self.base,'/api/v1/recipe-matches',{'ingredient_ids':[1]},headers={
            'Content-Type':'application/json','X-Requested-With':'recipe-platform','Origin':self.base})
        spec=request(self.base,'/openapi.json')
        self.assertIn('get',spec['paths']['/api/v1/ingredients'])
        self.assertIn('post',spec['paths']['/api/v1/recipe-matches'])
        self.assertIn('/api/v1/recipes/{recipe_id}',spec['paths'])

    def test_11_page_only_details_and_query_plans(self):
        with closing(sqlite3.connect(self.base_db)) as db:
            db.row_factory=sqlite3.Row
            statements=[]
            db.set_trace_callback(statements.append)
            db.execute('BEGIN')
            data=match_recipes(db,[21,30],1,1)
            self.assertEqual(db.total_changes,0)
            db.set_trace_callback(None)
            detail_queries=[q for q in statements if 'SELECT DISTINCT ri.recipe_id, i.id, i.name' in q]
            self.assertEqual(len(detail_queries),1)
            self.assertIn('IN (52)',detail_queries[0])
            self.assertEqual(data['total'],60)
            self.assertEqual(len(data['items']),1)
            plans={}
            for label,sql,params in (
                ('candidates_1_2_3_4',matched_cte([1,2,3,4])+'SELECT COUNT(*) FROM matched',[1,2,3,4]),
                ('ranking_1_2_3_4',ranking_sql([1,2,3,4]),[1,2,3,4,24,0]),
                ('ranking_21_30',ranking_sql([21,30]),[21,30,7,0]),
                ('page_details_52',details_sql([52]),[52]),
                ('lookup_blue',"SELECT id,name FROM ingredients WHERE name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?",[ingredient_pattern('blue'),20,0])):
                plans[label]=[dict(r) for r in db.execute('EXPLAIN QUERY PLAN '+sql,params)]
        (RUN/'query_plans.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2)+'\n')
        (RUN/'page_detail_trace.json').write_text(json.dumps(statements,ensure_ascii=False,indent=2)+'\n')

    def test_12_original_baseline_and_smoke(self):
        copied=RUN/'check_baseline.py'
        shutil.copyfile(ROOT/'scripts/check_baseline.py',copied)
        self.assertEqual(digest(copied),digest(ROOT/'scripts/check_baseline.py'))
        for label,script,expected in (('baseline_13',copied,'PASS: 13 baseline HTTP checks'),
                                      ('smoke_33',ROOT/'scripts/smoke_test.py','PASS: 33 HTTP checks')):
            command=[sys.executable,'-B',str(script),'--base',self.base]
            if label=='baseline_13': command += ['--output',str(RUN/'baseline_http.json')]
            result=subprocess.run(command,
                                  cwd=ROOT,capture_output=True,text=True,timeout=60)
            (RUN/f'{label}.log').write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn(expected,result.stdout)
            REGRESSIONS[label]={'exit_code':result.returncode,'result':expected}


if __name__=='__main__':
    required = ('data/stage1_sample.db', 'data/stage1_no_match.db', 'data/stage1/acceptance_expected.json')
    if any(not (ROOT/name).is_file() for name in required):
        raise SystemExit('Generate fixtures first: python scripts/prepare_ingredient_fixtures.py --recipes PATH --reviews PATH')
    ARTIFACTS.mkdir(exist_ok=True)
    RUN=Path(tempfile.mkdtemp(prefix='run-',dir=ARTIFACTS))
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(IngredientAcceptance)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    stop_servers()
    summary={'run_directory':str(RUN),'entrypoint':ENTRYPOINT,'tests_run':result.testsRun,
             'failures':len(result.failures),'errors':len(result.errors),
             'http_requests':len(HTTP_RECORDS),'regressions':REGRESSIONS,
             'servers':[{'pid':p.pid,'returncode':p.returncode} for p,_ in SERVERS],
             'examples':EXAMPLES}
    (RUN/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (RUN/'http_checks.json').write_text(json.dumps(HTTP_RECORDS,ensure_ascii=False,indent=2)+'\n')
    (ARTIFACTS/'latest_results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print('ARTIFACTS',RUN)
    print('HTTP_CHECKS',len(HTTP_RECORDS))
    raise SystemExit(0 if result.wasSuccessful() else 1)
