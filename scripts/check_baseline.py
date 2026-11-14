"""Thirteen baseline HTTP checks for the generated 100-recipe fixture."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--base', default='http://127.0.0.1:8767')
parser.add_argument('--output', type=Path, default=Path('data/baseline_http.json'))
args = parser.parse_args()
results = []


def get(path, expected=200):
    try:
        response = urllib.request.urlopen(args.base + path, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read()
    assert response.status == expected, (path, response.status, body[:200])
    item = {
        'path': path, 'status': response.status,
        'content_type': response.headers.get('Content-Type'),
        'request_id_present': bool(response.headers.get('X-Request-ID')),
    }
    data = (json.loads(body) if 'json' in (response.headers.get('Content-Type') or '')
            else body.decode('utf-8'))
    results.append(item)
    return data, item


health, item = get('/api/v1/health')
assert health == {'status': 'ok'}
item['result'] = health
stats, item = get('/api/v1/stats')
assert stats == {'recipes': 100, 'source_reviews': 815, 'ingredients': 335}
item['result'] = stats
search, item = get('/api/v1/recipes?q=chicken&max_minutes=30')
assert search['total'] == 0 and search['items'] == []
item['result'] = {'total': search['total'], 'ids': [r['id'] for r in search['items']]}
search, item = get('/api/v1/recipes?q=chicken')
assert search['total'] == 8
assert [r['id'] for r in search['items']] == [44, 49, 71, 101, 112, 114, 139, 144]
item['result'] = {'total': search['total'], 'ids': [r['id'] for r in search['items']]}
search, item = get('/api/v1/recipes?q=chicken&max_minutes=120')
assert search['total'] == 6
assert all(r['total_seconds'] is not None and r['total_seconds'] <= 7200
           for r in search['items'])
item['result'] = {'total': search['total'], 'ids': [r['id'] for r in search['items']]}
recipe, item = get('/api/v1/recipes/38')
assert recipe['id'] == 38 and len(recipe['ingredients']) == 4 and len(recipe['steps']) == 9
item['result'] = {'id': recipe['id'], 'name': recipe['name'],
                  'ingredients': recipe['ingredients'], 'steps': len(recipe['steps']),
                  'source_reviews': len(recipe['source_reviews'])}
invalid, item = get('/api/v1/recipes?page=0', 422)
assert invalid['code'] == 'VALIDATION_ERROR' and invalid.get('request_id')
item['result'] = invalid
missing, item = get('/api/v1/recipes/999999', 404)
assert missing['code'] == 'HTTP_404'
item['result'] = missing
html, item = get('/')
assert 'detail-dialog' in html and 'app.js' in html
item['result'] = 'HTML contains detail dialog and module script'
js, item = get('/app.js')
assert 'async function api(' in js and 'async function openRecipe(' in js
item['result'] = 'API wrapper and detail opener served'
css, item = get('/styles.css')
assert '.card' in css
item['result'] = 'Shared card styles served'
_, item = get('/docs')
item['result'] = 'Swagger UI served'
spec, item = get('/openapi.json')
assert spec['info']['version'] == '0.2.0'
item['result'] = spec['info']

output = args.output
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(results, ensure_ascii=False, indent=2))
print(f'PASS: {len(results)} baseline HTTP checks')
