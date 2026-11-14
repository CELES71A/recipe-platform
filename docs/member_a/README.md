# 成员 A：食材找菜交付与复现

已实现数据库食材选项、食材匹配 API 和页面；默认 `app.main:app` 可直接提供。食材按 ID 匹配，不做同义词推断；覆盖率为匹配的不同食材数／需要的不同食材数，排序为缺少数升序、未四舍五入覆盖率降序、食谱 ID 升序。

- [接口、规则与响应示例](api.md)
- [四类验收、全量结论及自动化证据摘要](verification.md)
- [给 D 的录屏食谱与步骤](demo.md)
- [全量 HTTP／精确分数核对与查询计划](full_review.json)
- [全量导入统计](import_report.json)
- [未采用的覆盖索引实验](index_experiment.json)

## 新克隆：生成测试所需内容

应用无需 Node/npm；只有 DOM 联调测试需要 Node。参考环境是 Python 3.14.6、SQLite CLI 3.51.0（FTS5）、Python SQLite 3.54.0、Node 26.4.0。依赖版本已锁定；下列命令从仓库根目录执行。macOS/Linux 使用示例路径；Windows 的 Python 路径按根 README 调整。自动化已在 Mac 验证，其他系统未实测。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-locked.txt
.venv/bin/python -m pip check
sqlite3 :memory: "CREATE VIRTUAL TABLE test USING fts5(name);"
```

课程提供的两份 CSV **不随 Git 分发**。自行准备原始 recipes.csv、reviews.csv，替换下列外部路径；Parquet 不用于本生成器。

```sh
.venv/bin/python -B scripts/prepare_ingredient_fixtures.py \
  --sqlite sqlite3 --recipes /path/to/recipes.csv --reviews /path/to/reviews.csv
```

该可提交脚本调用现有导入器选择前 100 道真实食谱，再独立按集合和精确分数生成：

```text
data/stage1_sample.db
data/stage1_no_match.db
data/stage1/acceptance_expected.json
```

无需从成员 A 电脑复制任何忽略文件。生成器检查数据集为预期的 100 食谱／335 食材／900 关联／872 步骤／815 来源评论；不匹配则报错，不用错误样本继续验收。已有样本或预期文件不会被覆盖；重复复现建议新克隆。`--limit 100` 仍会扫描完整评论文件；导入必须串行，`data/prepared/` 是同仓库共用中间目录，先保留需要的旧内容。

## 自动化检查

```sh
.venv/bin/python -B scripts/test_ingredient_api.py
npm ci --prefix tests/ui --ignore-scripts
node scripts/test_ingredient_ui.mjs
```

API 脚本执行 12 组检查、119 次直接 HTTP 请求，并调用已提交的 `scripts/check_baseline.py` 进行原 13 项基线、原 `scripts/smoke_test.py` 进行 33 项冒烟。它们自动创建独立副本、启动默认入口和关闭服务。DOM 脚本使用 `tests/ui/package-lock.json` 锁定的 jsdom 31.0.1，进行 15 组真实接口＋DOM 交互检查；它不是浏览器渲染测试。其 Python 默认为仓库 `.venv`，可用 `INGREDIENT_TEST_PYTHON` 指向其他环境。

结果写入 `data/stage2/run-*/`、`data/stage3/run-*/`，不提交 Git。测试目录缺失时脚本会明确提示先生成样本／安装工具。基线脚本已在仓库内，不再读取被忽略的旧脚本。

## 运行页面

```sh
APP_DB_PATH=data/stage1_sample.db .venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8000
```

访问 http://127.0.0.1:8000/，选择「食材找菜」。接口文档在 `/docs`。关闭服务使用 Ctrl+C。APP_DB_PATH 相对仓库根目录解析；不要让测试脚本指向个人正式库。

## 全量复核复现

使用未占用的独立数据库路径，保留样本库和原始文件：

```sh
.venv/bin/python -B scripts/import_data.py --sqlite sqlite3 \
  --recipes /path/to/recipes.csv --reviews /path/to/reviews.csv \
  --db data/full_review.db --limit 0
APP_DB_PATH=data/full_review.db .venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8770
```

另开终端：

```sh
.venv/bin/python -B scripts/review_ingredient_full.py --db data/full_review.db \
  --base http://127.0.0.1:8770 --output data/full_review.json
```

全量脚本只读库，以不同于实现的全关联聚合和精确 Fraction 排序作核对，检查 total、整页顺序、数量、明细、不同分母的完全匹配，以及完整性／外键；记录常见、少见、多选、翻页的三个 HTTP 耗时样本和 EXPLAIN。不会写账户。样本断言的 13 项基线在样本副本执行，不错误套用到全量总数。

## 共享文件与合并

共享改动仅为 `backend/app/main.py` 静态挂载前两行注册，`frontend/index.html` 的 CSS／导航／独立 section，`frontend/app.js` 的 import／section 列表／初始化事件，以及本说明在根 README 的入口。`frontend/styles.css`、旧 api/openRecipe 内容不变。新 JS/CSS、后端模块、测试和文档为 A 独立文件。

B 的导航、页面切换或共用视觉变更应在合并时核对；维护者审核 PR 后合并。历史 `docs/member_a_router_registration.patch` 已应用，不应再次应用；临时 `scripts/ingredient_test_app.py` 仅保留为早期隔离验收入口，默认运行和最终验收均不用它。无新增数据库迁移；001/002 保持原样。
