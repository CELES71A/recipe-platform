# 食谱与烹饪助手

JavaScript 前端 + FastAPI 后端 + SQLite 数据库。DB Browser for SQLite 用于查看和管理数据库；独立 sqlite3 命令行程序负责导入；Python 的 sqlite3 模块是后端读取同一文件的连接驱动。

所有命令均在项目根目录（README 所在目录）执行，路径不依赖成员电脑。Python 3.11+。原生 JavaScript 前端由后端统一提供，无需 npm。

## 1. 安装依赖

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

已有虚拟环境可跳过创建步骤。macOS/Linux 使用 python3 创建环境，后续将 .\.venv\Scripts\python.exe 替换为 .venv/bin/python。

## 2. 准备独立 SQLite 命令行工具

Windows 可从 https://www.sqlite.org/download.html 下载 SQLite Tools 的 Windows x64 ZIP，将其中 sqlite3.exe 放入项目 tools/ 目录。其他系统可安装 sqlite3 命令行程序并放入 PATH。

```powershell
.\tools\sqlite3.exe -version
.\tools\sqlite3.exe :memory: "CREATE VIRTUAL TABLE test USING fts5(name);"
```

第二条命令执行成功表示工具支持 FTS5。项目使用支持 FTS5 的 SQLite CLI，工具存放于 tools/ 目录。每位成员自行准备本地工具，二进制文件由 Git 忽略。

## 3. 通过命令导入全部食谱与评论

将数据文件放入：

```text
data/raw/recipes.csv
data/raw/reviews.csv
```

关闭正在编辑该数据库的 DB Browser 写事务，停止后端服务，再执行：

```powershell
.\.venv\Scripts\python.exe scripts/import_data.py --sqlite tools/sqlite3.exe
```

默认导入全部食谱及其对应评论。导入器先将原始 CSV 中的 R 列表、时长和缺失值转换为规范 CSV，然后启动独立 sqlite3.exe，执行 .import 和 SQL 合并，最终得到 data/recipe_platform.db。Python 在这里负责清洗和调度，真正导入由 SQLite CLI 执行。数据库文件可直接用 DB Browser 打开。

课程原始数据包含 522,517 条食谱、1,401,982 条评论。完整导入后，source_reviews 保存 1,401,963 条可关联食谱的评论，quarantined_reviews 保存 19 条孤儿评论。实际导入统计见 data/prepared/import_report.json。导入需要数分钟及数 GB 可用空间，终端会输出进度。不要在运行中再次启动另一个导入任务。

只想快速联调，可使用独立测试库：

```powershell
.\.venv\Scripts\python.exe scripts/import_data.py --sqlite tools/sqlite3.exe --limit 10000 --db data/sample.db
```

--limit 0 表示全部；指定正整数时仅选择前 N 个食谱及其对应评论，未选择的评论不会误记为孤儿。--recipes、--reviews 可指定其他输入位置，含空格的路径请加引号。

导入生成：

- data/prepared/import.sql：实际执行的 CLI 导入脚本。
- data/prepared/import_report.json：数量、耗时、0 分及隔离统计、完整性检查结果。
- data/prepared/errors.jsonl：食谱解析错误及原文。
- data/prepared/*.csv：规范化中间数据。
- import_runs 表：导入批次状态；quarantined_reviews 表：孤儿评论及原文。

重复导入按来源 ID 更新，不重复增加记录；食材和步骤按当前导入范围重建，本站账户、收藏、评价和进度不清空。来源文件必须来自同一数据集，ID 与本系统用户 ID 不混用。中间文件和原始文件均不提交 Git。

部分来源食材数量与用量数量不一致。此时业务用量标记未知，原始完整字段保存在 source_recipe_records.raw_json，绝不强行错位配对。历史 0 分保留 raw_rating=0，规范评分置 NULL。营养值保留原文，页面提示口径未核实。

项目默认数据库文件为 data/recipe_platform.db。

## 4. 在 DB Browser 中检查数据库

1. 打开 DB Browser for SQLite，点击“打开数据库 / Open Database”。
2. 选择项目 data/recipe_platform.db，不要选 data/raw 中的 CSV。
3. 在“数据库结构 / Database Structure”查看 recipes、recipe_steps、ingredients、recipe_ingredients、source_reviews 等关系表。
4. 在“浏览数据 / Browse Data”选择 recipes，查看真实食谱；source_reviews 是来源评论，reviews 是网站用户新增评价。
5. 切换到“执行 SQL / Execute SQL”，执行下列语句：

```sql
SELECT COUNT(*) AS recipe_count FROM recipes;
SELECT COUNT(*) AS source_review_count FROM source_reviews;
SELECT COUNT(*) AS isolated_review_count FROM quarantined_reviews;
SELECT * FROM import_runs ORDER BY id DESC;
SELECT id, name, total_seconds FROM recipes LIMIT 10;
SELECT r.name, s.position, s.instruction
FROM recipes r JOIN recipe_steps s ON s.recipe_id = r.id
WHERE r.id = 38 ORDER BY s.position;
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

integrity_check 应返回 ok；foreign_key_check 应没有结果。SQL 查询每条带分号。不要把 .import、.read 等 CLI 专用命令粘贴到 DB Browser 的 SQL 编辑器。

需要手动写入时先执行 PRAGMA foreign_keys=ON，并使用 DB Browser 的“写入更改 / Write Changes”提交或“撤销更改 / Revert Changes”回滚。避免在网页操作期间长时间保留图形工具中的写事务，否则可能出现 database is locked。正常查看可以与后端同时进行。

如果 DB Browser 打开 FTS5 表时报缺少模块，请升级到支持 FTS5 的版本，不要删除搜索表。不要手动删除数据库的 -wal 或 -shm 文件。备份应使用工具的数据库备份/导出能力或 SQLite backup 接口。

## 5. 启动网站并实际操作

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

- 页面：http://127.0.0.1:8000/
- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/v1/health

不要直接双击 HTML。前端和 API 同源。端口占用时加 --port 8001，并访问对应端口。Ctrl+C 停止服务。

建议演示流程：

1. 搜索 chicken，筛选分类、30 分钟内或来源评分，翻页浏览。
2. 打开详情，查看图片、食材、用量、步骤、营养原值及来源评论。
3. 注册自己的邮箱和至少 8 位密码；登录状态保存在 HttpOnly Cookie，密码以 PBKDF2 哈希存储。
4. 收藏食谱，进入“我的收藏与记录”查看；刷新页面后仍保留。
5. 开始烹饪，切换步骤、暂停；关闭后在个人记录恢复，明确点击“完成烹饪”结束。
6. 提交 1–5 分及可选评论；重复提交会更新本人的评价，个人页可以删除。本地均分与来源评分分开展示。
7. 退出后不能继续写收藏或评价。不同账号只看到各自的收藏、评价和烹饪记录。

收藏、评价和烹饪操作需要登录；登录成功后，点击相应按钮完成操作。

## 6. 数据库连接配置

默认直接使用项目内 data/recipe_platform.db，无需设置。需要切换测试库时，在导入和启动服务的同一终端设置：

```powershell
$env:APP_DB_PATH = 'data/sample.db'
```

相对路径统一相对于项目根目录。导入时显式 --db 会覆盖该环境变量。macOS/Linux 使用 export APP_DB_PATH='data/sample.db'。程序不会自动加载 .env。

数据库结构通过 backend/migrations 编号 SQL 管理；后端启动时按 schema_migrations 应用缺失版本。迁移在事务中执行。首次 CLI 导入也会应用相同迁移，DB Browser 打开的是同一份数据库。

## 7. 管理员

项目维护成员通过命令创建管理员账户：

```powershell
.\.venv\Scripts\python.exe scripts/create_admin.py admin@example.com
```

按提示输入自己的密码（不回显）。该命令创建或提升账户，并更新密码；仅由项目维护成员使用。登录后出现“管理”入口，可查看导入批次并按食谱 ID 发布/下架/转草稿。普通用户即使直接调用管理 API 也会被拒绝。

## 8. 结构与协作规范

```text
backend/app/          # main.py API、security.py 认证、database.py 连接与迁移
backend/migrations/   # 编号 SQL 迁移
frontend/             # HTML、CSS、JavaScript 原生模块
scripts/              # import_data.py CLI 导入、create_admin.py 管理员工具
docs/                 # 数据库设计、测试记录
data/raw/             # 原始 CSV（忽略 Git）
data/prepared/        # 导入中间数据与报告（忽略 Git）
tools/                # 本地 SQLite CLI（忽略 Git）
```

Python 函数/文件使用 snake_case，JavaScript 变量/函数使用 camelCase，类使用 PascalCase。SQL 表为复数 snake_case，主键 id、外键 entity_id。API 统一 /api/v1 前缀。所有 SQL 用户参数均绑定，不拼接用户输入。

成员各用本地数据库；提交源码、SQL、依赖和文档，不提交数据库、原始文件、虚拟环境、IDE 设置、会话和密码。不要修改已经共享执行过的迁移；结构变化增加下一版本。更改接口时同步页面与 /docs 契约。

## 9. 功能说明

| 功能 | 操作与数据说明 |
| --- | --- |
| 食谱搜索 | 使用 FTS5 按英文单词检索菜名，支持分类、总时长、来源评分筛选及分页 |
| 食谱详情 | 展示图片、食材、用量、制作步骤、营养原值及来源评论；外部图片失效时使用占位显示 |
| 注册与登录 | 使用邮箱和密码注册、登录、退出；登录状态通过 HttpOnly Cookie 保存 |
| 收藏 | 食谱保存至默认收藏夹，个人页面支持查看和取消收藏 |
| 评分与评论 | 支持提交 1–5 分、填写评论、更新本人评价和删除本人评价 |
| 烹饪模式 | 支持切换步骤、暂停、恢复及完成；步骤进度写入数据库，通过版本号校验更新冲突 |
| 个人记录 | 展示当前账户的收藏、评价和烹饪记录 |
| 管理 | 管理员查看导入批次，并设置食谱为发布、草稿或下架状态 |

指定总时长上限时，结果仅包含已知时长且符合条件的食谱。搜索评分筛选和排序使用来源评分；详情中的本站评分单独计算。营养数据以来源原值展示，并注明单位和每份口径待核实。

本地开发采用 HTTP。通过 HTTPS 部署时，使用 APP_SECURE_COOKIE=1 启用安全 Cookie。

参考：[SQLite CLI](https://www.sqlite.org/cli.html)、[SQLite 外键](https://www.sqlite.org/foreignkeys.html)。

## 10. 导入重试与测试

已完成清洗但写库失败时，可检查原因后复用中间文件重试，无需再次解析原始 CSV：

```powershell
.\.venv\Scripts\python.exe scripts/import_data.py --sqlite tools/sqlite3.exe --load-prepared
```

必须使用生成这批中间文件时的同一个数据库，不能在多个数据库之间复用 import.sql（它记录了该次导入的映射和批次 ID）。通常优先重跑正常导入命令，只有确定中间文件完整时才使用该重试选项。

接口冒烟测试会生成测试账户和记录，务必使用独立测试库。例如先按第3节导入 data/sample.db，再打开一个终端：

```powershell
$env:APP_DB_PATH = 'data/sample.db'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8767
```

另一个终端运行：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_test.py --base http://127.0.0.1:8767
```

详细检查范围见 docs/database_design.md。运行完成后使用 Ctrl+C 停止测试服务。正式使用时清除测试终端的 APP_DB_PATH，或重新打开终端，以免连到 sample.db。

