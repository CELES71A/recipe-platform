# 给 D：验收与录屏

先按 [复现说明](README.md) 生成样本库，启动默认入口并指向 `data/stage1_sample.db`。这 100 道均来自真实原始数据，演示数量确定；不要把样本的 15／8 道结果解说成全量数量。视频记录使用的 Git 提交号。

## 同一道食谱从选择到详情

1. 首页点击「食材找菜」。依次搜索并选中**恰好**为 blueberries、granulated sugar、vanilla yogurt、lemon juice 的四项。不要误选 fresh/frozen blueberries 或 fresh lemon juice。
2. 点击“找菜”。首卡应是 **38 — Low-Fat Berry Blue Frozen Dessert**：已匹配 4、还缺少 0、100.0%，共 15 道候选。
3. 打开该卡详情，核对名称、4 种食材、9 个步骤。关闭，确认 4 个标签和结果仍在。
4. 移除 vanilla yogurt 与 lemon juice，再找菜。同一食谱匹配 2、缺少 2、50.0%，共 8 道候选。已有和缺少清单对应刚才操作。
5. 可打开同一道详情，接续原项目的登录、收藏、开始烹饪、暂停／恢复与评价流程。此部分沿用 B/D 的既有流程，不将匹配 API 说成写库操作。

对应 ID（记录用途，页面按名称选）：1 blueberries、2 granulated sugar、3 vanilla yogurt、4 lemon juice。用量分别为 4、1/4、1、1；不推断来源单位。

## 可选的精度／重复食材片段

- 只选 sugar（ID 30）、instant coffee（ID 90）：食谱 **52 — Cafe Cappuccino** 需要 3 种，匹配 2，缺 nonfat dry milk solid（91），显示 66.7%。
- 食谱 **43 — Best Blackbottom Pie** 有 13 个食材位置，sugar 在 2、4、13；不同食材只有 11。自动验收确认选 sugar 只计匹配 1、缺 10，覆盖率约 9.1%。

## 无匹配与错误

另一个终端将默认入口指向 `data/stage1_no_match.db`、使用另一个端口（例如 8001）。只选 vanilla yogurt，应为合法 200 空结果，不是未知食材。这个独立副本只下架食谱 38；主样本库不改变。

空选择时点击找菜应明确提示；失败重试可在保持页面选择时停止／恢复测试服务复核。键盘使用 Tab、Shift+Tab、Space、Enter；详情 Escape 关闭。Mac 用户已手动确认主流程、窄屏、键盘、失败重试、合法无匹配五项通过；未提供具体浏览器版本或截图，不补造这些元数据。

## 全量库演示差异

全量四项输入候选 48,970，道菜 38 仍在完整匹配首位；部分输入 `[1,2]` 候选 17,958，道菜 38 在每页 12 条的第 18 页。这是新候选进入完整排序后的正常结果。若要连贯展示同一道菜的完全／部分匹配，优先使用上面的真实样本库；全量规模、耗时和查询计划由 C/B 使用验证摘要展示。
