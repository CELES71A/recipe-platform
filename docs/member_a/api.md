# 食材找菜接口

## GET /api/v1/ingredients

公开读取，返回数据库食材选项，包含只有下架食谱关联的合法食材。

| 参数 | 约束／默认 |
|---|---|
| q | 最多 100 字符，默认空；小写、去首尾／合并连续空白，名称包含搜索 |
| page | 整数 ≥1，≤9223372036854775807，默认 1 |
| page_size | 1–100，默认 20 |

`%`、`_`、反斜杠按字面转义，SQL 参数绑定；顺序为 name COLLATE NOCASE、id。匹配零项和超末页均为 200 空 items，保留 total。以下示例来自可重建的 100 食谱样本；全量 blue 的 total 是 30。

```http
GET /api/v1/ingredients?q=blue
```

```json
{"items":[{"id":214,"name":"blue cheese"},{"id":1,"name":"blueberries"},{"id":280,"name":"fresh blueberries"},{"id":123,"name":"frozen blueberries"}],"total":4,"page":1,"page_size":20}
```

## POST /api/v1/recipe-matches

公开只读。请求头 `Content-Type: application/json`、`X-Requested-With: recipe-platform`；带 Origin 时必须同源。复用现有 CSRF、校验和错误处理，不要求登录。

| JSON 字段 | 约束／默认 |
|---|---|
| ingredient_ids | 必填数组，原始长度最多 100；元素为严格正 int64，拒绝 bool／浮点／字符串转换；空数组专门提示 |
| page | 严格正 int64，默认 1 |
| page_size | 严格整数 1–100，默认 24 |

拒绝额外字段。输入去重、全量验证是否存在；任意未知 ID 拒绝整个请求。仅候选 published 食谱，至少命中一种所选食材。食谱内不同位置同一 ID 只计一次；分母是全部所需的不同食材，不是已选项数量。

`matched_count + missing_count = required_count`；coverage 为 matched_count / required_count，0–1，后端不预先四舍五入。先按缺少数升序、实际覆盖率降序、食谱 ID 升序完整排序，之后分页；明细按食材 ID 升序。全部 100% 的菜无论所需数量多少都按 ID 排序。

请求与样本实际响应：

```json
{"ingredient_ids":[1,2],"page_size":1}
```

```json
{"ingredient_ids":[1,2],"items":[{"id":38,"name":"Low-Fat Berry Blue Frozen Dessert","required_count":4,"matched_count":2,"missing_count":2,"coverage":0.5,"matched_ingredients":[{"id":1,"name":"blueberries"},{"id":2,"name":"granulated sugar"}],"missing_ingredients":[{"id":3,"name":"vanilla yogurt"},{"id":4,"name":"lemon juice"}]}],"total":8,"page":1,"page_size":1}
```

合法无候选（无匹配副本）：

```json
{"ingredient_ids":[3],"items":[],"total":0,"page":1,"page_size":24}
```

HTTP 错误均含 code、message、request_id；以下 request_id 为示例：

```json
{"code":"HTTP_422","message":"未知食材 ID：999999","request_id":"example-request-id"}
```

| 情况 | HTTP／code | message |
|---|---|---|
| 空数组 | 422 / HTTP_422 | 请至少选择一种食材 |
| 未知 ID | 422 / HTTP_422 | 未知食材 ID：999999（多个去重、升序列出） |
| 参数类型／范围／长度或 JSON 非法 | 422 / VALIDATION_ERROR | 请检查必填项、长度和数值范围 |
| `(page-1)*page_size` 超 int64 | 422 / HTTP_422 | 分页偏移超出 SQLite 整数范围 |
| 缺校验头／Origin 错误 | 403 / CSRF_REJECTED | 请求来源校验失败 |
| SQLite 操作不可用 | 503 / DATABASE_UNAVAILABLE | 沿用原项目数据库不可用提示 |

合法超末页保留 total、返回空 items；不会把超范围 ID 或偏移传给 sqlite3 造成服务器异常。详情继续使用 `GET /api/v1/recipes/{id}`；匹配结果中的 id 可直接交给旧 openRecipe。
