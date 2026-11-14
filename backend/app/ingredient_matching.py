"""Read-only ingredient lookup and recipe matching against the existing schema."""

SQLITE_MAX_INTEGER = 2**63 - 1


class UnknownIngredientsError(ValueError):
    def __init__(self, ingredient_ids):
        self.ingredient_ids = ingredient_ids
        super().__init__('未知食材 ID：' + ', '.join(map(str, ingredient_ids)))


def placeholders(values):
    # Only placeholder count is interpolated; all values remain bound parameters.
    return ','.join('?' for _ in values)


def ingredient_pattern(q):
    normalized = ' '.join(q.lower().split())
    escaped = normalized.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + escaped + '%'


def find_ingredients(db, q, page, page_size):
    pattern = ingredient_pattern(q)
    clause = " FROM ingredients WHERE name LIKE ? ESCAPE '\\'"
    total = db.execute('SELECT COUNT(*)' + clause, (pattern,)).fetchone()[0]
    offset = (page - 1) * page_size
    rows = [] if offset >= total else db.execute(
        'SELECT id,name' + clause + ' ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?',
        (pattern, page_size, offset),
    ).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total,
            'page': page, 'page_size': page_size}


def matched_cte(ingredient_ids):
    return f"""
        WITH matched AS (
            SELECT ri.recipe_id, COUNT(DISTINCT ri.ingredient_id) AS matched_count
            FROM recipe_ingredients AS ri
            JOIN recipes AS r ON r.id=ri.recipe_id
            WHERE ri.ingredient_id IN ({placeholders(ingredient_ids)})
              AND r.status='published'
            GROUP BY ri.recipe_id
        )
    """


def ranking_sql(ingredient_ids):
    return matched_cte(ingredient_ids) + """
        , counts AS (
            SELECT m.recipe_id, m.matched_count,
                   COUNT(DISTINCT needed.ingredient_id) AS required_count
            FROM matched AS m
            JOIN recipe_ingredients AS needed ON needed.recipe_id=m.recipe_id
            GROUP BY m.recipe_id
        )
        SELECT r.id, r.name, c.required_count, c.matched_count,
               c.required_count-c.matched_count AS missing_count,
               1.0*c.matched_count/c.required_count AS coverage
        FROM counts AS c JOIN recipes AS r ON r.id=c.recipe_id
        ORDER BY missing_count ASC, coverage DESC, r.id ASC
        LIMIT ? OFFSET ?
    """


def details_sql(recipe_ids):
    return f"""
        SELECT DISTINCT ri.recipe_id, i.id, i.name
        FROM recipe_ingredients AS ri
        JOIN ingredients AS i ON i.id=ri.ingredient_id
        WHERE ri.recipe_id IN ({placeholders(recipe_ids)})
        ORDER BY ri.recipe_id, i.id
    """


def match_recipes(db, ingredient_ids, page, page_size):
    """Caller validates nonempty positive int64 IDs and starts a read transaction."""
    selected = sorted(set(ingredient_ids))
    known = {row[0] for row in db.execute(
        f'SELECT id FROM ingredients WHERE id IN ({placeholders(selected)})', selected,
    )}
    unknown = sorted(set(selected) - known)
    if unknown:
        raise UnknownIngredientsError(unknown)

    total = db.execute(matched_cte(selected) + 'SELECT COUNT(*) FROM matched', selected).fetchone()[0]
    result = {'ingredient_ids': selected, 'items': [], 'total': total,
              'page': page, 'page_size': page_size}
    offset = (page - 1) * page_size
    if offset >= total:
        return result

    rows = db.execute(ranking_sql(selected), selected + [page_size, offset]).fetchall()
    items = [dict(row, matched_ingredients=[], missing_ingredients=[]) for row in rows]
    by_id = {item['id']: item for item in items}
    # One detail query per page, never one per recipe or for off-page candidates.
    for row in db.execute(details_sql(by_id), list(by_id)):
        field = 'matched_ingredients' if row['id'] in known else 'missing_ingredients'
        by_id[row['recipe_id']][field].append({'id': row['id'], 'name': row['name']})
    result['items'] = items
    return result
