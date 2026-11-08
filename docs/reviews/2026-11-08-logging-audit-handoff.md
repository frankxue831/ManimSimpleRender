# 渲染日志重检：固定版本交付清单

交付日期：2026-11-08（Asia/Shanghai）。状态：**代码与本地证据已固定，等待 Grok 独立复核和用户验收**。

本清单用于人工交接。没有向 Grok 发送消息、接入 webhook、创建 PR、推送或合并。
本地测试成功、任务完成或通知被接收，均不等于独立复核通过、负责人接单或最终验收。

## 1. 审查对象与基线

| 项目 | 固定值 |
| --- | --- |
| 基线提交 | `c449fd296751bd6e525d31e952152d6e84b03221` |
| 实现提交（本轮代码审查对象） | `6837039cffec4689119d602453236d36ac42487e` |
| 实现提交的 tree | `1679535c10acc99073c1a995e302d970253e71b1` |
| 本地分支 | `codex/audit-render-logging-20261108` |
| 实现改动 | 26 个文件，2463 行新增、443 行删除；部分差异来自阶段作用域缩进 |
| 清单版本 | 包含本文件的后续文档提交；它不改变上述实现提交 |

评审时使用完整提交哈希，不使用可能继续变化的分支名或“最新版”。本文件作为单独的
文档提交保存，避免在实现提交中循环引用自身哈希。最终交付消息另列文档提交哈希。

```sh
git show --stat 6837039cffec4689119d602453236d36ac42487e
git diff c449fd296751bd6e525d31e952152d6e84b03221 \
  6837039cffec4689119d602453236d36ac42487e -- app explainer scene_production tests
git rev-parse 6837039cffec4689119d602453236d36ac42487e^{tree}
```

## 2. 修改范围

| 范围 | 主要文件 | 行为变化 |
| --- | --- | --- |
| 通用阶段记录 | `app/render/diagnostics.py` | 私有 JSONL；UTC 观察时间、进程内单调时钟耗时、阶段及固定异常分类；可选记录故障隔离 |
| GUI | `app/render/worker.py`、`app/ui/controller.py`、`app/ui/main_view.py` | 逐行时间戳、批次/任务起止及结果、稀疏输出定时刷新、完整磁盘日志导出、丢日志警告、强制退出时停止刷新线程 |
| Production / reinspection | `scene_production/__init__.py`、`scene_production/reinspection.py` | 准备/渲染/产物/检查/发布阶段；区分渲染超时、取消、工具超时及其他失败；原始日志逐行刷新 |
| Agent | `scene_production/agent.py` | 原有事件增加时间和阶段；独立阶段文件；早期异常 traceback 留在私有 `api.log`；固定失败原因 |
| Handoff | `scene_production/handoff/runner.py` | 启动/等待/排空/校验/状态写入时间线；unknown 的具体阶段及原因；传递白名单内的 Agent 失败原因 |
| Explainer | `explainer/render/adapter.py` | 预检查通过后持久保留私有原始日志和阶段时间线，包括异常失败路径 |
| 测试和文档 | `tests/`、`docs/`、`README.md`、`.gitignore` | 故障、隐私、线程、导出和真实渲染回归；日志目录说明；忽略新建的 Explainer 日志目录 |
| 引用示例 | `examples/citation-gate.story.yaml` | 更新因 adapter 代码移动而变化的证据行号，不改变示例含义 |

完整文件列表可由 `git diff --name-only BASE CODE_COMMIT` 获取。`.venv/`、`media/`、
`output/`、原始日志、视频、数据库及缓存没有加入提交。没有修改 handoff ledger 的
授权、领取、fencing 或恢复状态机；没有实现自动重试、外部通知或自动验收。

## 3. 已有验证与证据

最终回归环境：macOS、Python 3.13.0、Manim 0.20.1、ffmpeg/ffprobe 9.0.2、Qt offscreen。
依赖仅安装在本工作树 `.venv`；系统已有 ffmpeg、Cairo 和 Pango。

| 检查 | 已观察结果 |
| --- | --- |
| 全套 unittest | 1187 项：1176 通过，11 跳过；36.126 秒 |
| 实际执行窗口 | 2026-11-08 10:37:22 至 10:37:59，Asia/Shanghai |
| Black / Flake8 | 仓库 CI 所列源码及示例路径通过；结果保存在本会话工具记录中 |
| `git diff --check` | 通过 |
| 引用示例 | `examples/citation-gate.story.yaml` 的 verify 和示例测试通过 |
| 真实 core smoke | 实际 Manim 生成非空 MP4，通过 |
| 新增真实日志 e2e | Agent、handoff 各自成功及场景抛异常；共 2 个测试方法、4 次真实渲染，通过 |
| 保留的额外 smoke | Agent 成功、场景异常、进入 `construct` 后超时、成功复检；GUI worker 的真实成功/失败记录，通过 |

11 个跳过项：9 个需要未安装的 `manim_voiceover`，1 个要求真实 Windows Job Object，
1 个 Qt 滚动区域在当前环境不可滚动。GUI worker 的保留 smoke 使用同步驱动；实际
QThread 调度和强制终止由独立子进程回归测试覆盖，不声称完成了人工 GUI 验收。

全套测试在本地提交前执行。提交前核对显示没有 Python 源码晚于该轮测试开始时间的修改；
本地实现提交保留了当时已验证的源码。文件时间仅是辅助线索；本清单和 SHA-256 是本地
完整性记录，不是签名 CI 证明，也不能替代审查者对指定提交的独立复跑。

### 本机证据位置

相对于仓库根目录，独占证据目录为：

`output/logging-audit-real-smoke-8drze745/`

| 文件 | 用途 |
| --- | --- |
| `summary.json` | 场景结果、退出码、耗时、阶段和产物相对路径 |
| `agent-success/`、`agent-failure/`、`agent-timeout/`、`agent-reinspection/` | Agent 状态、两事件 journal、私有 API 日志和 production 记录 |
| `gui-worker/render-*.log` | GUI worker 的真实成功与失败日志 |
| `review-evidence/full-tests.txt` | 最终全量测试逐项记录 |
| `review-evidence/baseline-real-smoke.txt` | 原有真实 core smoke 结果 |
| `review-evidence/retained-real-smoke.txt`、`review-evidence/retained-smoke.py` | 额外保留 smoke 的执行摘要及复现脚本 |
| `review-evidence/manifest.json` | 基线/实现/tree、26 个实现文件的 SHA-256、证据文件 SHA-256 和长度 |

这些文件只在本机存在，没有上传或包含在 Git 提交中。读取前先核对摘要：

| 对象 | SHA-256 |
| --- | --- |
| `summary.json` | `8913cc4d7960433b7c6422bff495845e408f3dfbdc8de6da4653828c0018c3fd` |
| `review-evidence/full-tests.txt` | `6ef11f4955ec797272c2f872ee6edc1c1b9a1a1b72c5e810e755042b9456f103` |
| `review-evidence/manifest.json` | `90ddc4e3145669bc634aad33bede171468943f8ed45100dddf1179dce375045c` |

Smoke 使用专门构造的场景和隐私测试标记，不含历史 EP3 输入。真实 handoff e2e 的
授权记录明确标注为合成测试 fixture，不是用户授权。完整原始日志、路径、源代码和
实际工作流授权仍应当作私有材料处理；如需向外部审查者提供内容，先选取必要材料并确认
传递范围，不直接分享整个工作目录。

## 4. 独立审查与修复记录

已有独立代码检查来自本会话派生的审查任务，**尚不包含 Grok Bot 的外部复核**。

| 本地复核发现 | 已落实修复 | 回归 |
| --- | --- | --- |
| 强制停止 QThread 会绕过 `finally`，新增刷新线程可能残留 | `finished` 直接触发停止/等待；刷新线程限时获取锁，避免遗留锁阻塞退出 | 隔离进程覆盖渲染阻塞、持有日志写锁两种强制终止，确认删除 worker 后没有残留刷新或输出 |
| Save Log 选择原路径/硬链接时，已知不完整警告被普通提示覆盖 | 同文件保护分支保留不完整警告及错误样式，不改写日志 | 原路径、硬链接、原字节保留和警告样式均覆盖 |
| 新增 adapter 代码移动使引用示例行号失效 | 更新到实际代码行 | 示例测试和 `explainer verify` 通过 |

这些修复均已包含在实现提交 `6837039cffec4689119d602453236d36ac42487e` 中。
第二轮独立检查未发现范围内其他阻塞问题；这不是对所有运行环境或所有风险的保证。

## 5. Grok 独立复核验收清单

审查结论应写明完整目标提交、所读文件、执行命令和证据。以下条目均待外部复核确认：

- [ ] 日志能回答何时开始、当前/失败阶段、原因、产物位置；阶段跨文件可通过 task、attempt、token、run ID 关联。
- [ ] 正常领取的 Agent render/inspect 仍恰好输出 `started` 与 `terminal`，token 一致；参数错误、领取前失败和 describe 仍可仅有 terminal。
- [ ] 成功依赖 API 完成、退出码、持久状态/journal 和产物校验。临时 `result.json.status=failed` 与诊断阶段结束都不决定终态或语义验收。
- [ ] `unknown` 不自动重试，不绕过或释放现有 fencing、槽位和恢复限制；诊断原因不被当作进程已全部停止的证明。
- [ ] 可选时间线及 GUI/Explainer 原始日志失败只告警或标记；production 原始日志与 Agent capture 保留既有错误行为；必需状态持久化失败仍保守处理为 unknown。
- [ ] 结构化阶段文件不含环境值、授权原文、任意异常消息或原始输出；raw 日志/traceback 留在私有文件。标识符仍可能敏感，不能宣称自动脱敏。
- [ ] UTC 是观察时间；耗时来自进程内单调时钟。嵌套、并行阶段不能直接相加，空档不能推断为人工等待或空闲。
- [ ] 稀疏输出及时可见；强制终止后辅助线程退出；Save Log 保留完整磁盘快照、同文件保护和不完整警告。
- [ ] 实际渲染成功、场景失败、超时、复检及 handoff 结果与日志一致；真实 MP4/抽帧存在且哈希匹配。
- [ ] 下列未验证边界被接受或列出补充验证要求，没有将测试通过直接当作用户验收。

建议在同一主机的独立 checkout/worktree 对实现提交复跑，以免与实现者共写工作树。
使用固定提交对应的 `AGENTS.md` 和依赖；最少执行：

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python -m unittest \
  tests.e2e.test_real_render_smoke tests.e2e.test_real_logging_smoke -v
python -m black --check app explainer scene_production tests main.py
python -m flake8 app explainer scene_production tests main.py
```

真实烟测会在工具链缺失时跳过，审查结果必须区分“通过”和“跳过”。CI 还检查若干示例
Python 文件，完整路径以固定提交中的 `.github/workflows/ci.yml` 为准。

## 6. 未验证项与验收条件

尚未覆盖：真实 TTS、OpenGL、完整人工 GUI 操作验收、真实 Windows/macOS 强制断电或
重启恢复、长时间磁盘阻塞、远程交付/Grok webhook、真实人类视觉/语义审核。历史 EP3
缺失的准备和人工等待日志不能补造。无日志轮转、保留期限或每事件 fsync 保证。

Handoff 在提交 ledger 结果前检查 `diagnostics_incomplete`。之后发生的最终事件或关闭
失败只会告警，不能回写已提交结果；缺少该字段不证明时间线绝对完整。

接受本轮改动至少需要：

1. 审查者对本清单中的完整实现哈希作出明确结论，完成或说明上述各项。
2. 阻塞性问题已修复并复测；不能复现或暂不修复的问题写出理由和影响，由用户决定是否接受。
3. 每次修复产生新代码哈希并补充验证记录；旧提交的通过结论不自动适用于新代码。
4. 用户明确接受剩余未验证项并决定后续 PR/合并。当前交付状态不包含该决定。

后续问题建议记录为：`问题编号 / 严重程度 / 目标提交 / 文件与行号 / 复现步骤 /
实际影响 / 处理决定 / 修复提交 / 验证结果 / 再审提交与结论`。

协作顺序为：用户确认目标与范围 -> 固定代码和证据 -> 用户转交 Grok 独立复核 ->
实现者复现/修复/复测 -> 对新哈希再审 -> 用户验收并决定是否合并。自动通知和自动执行
仅在人工交接跑通后另行设计，不在此次交付中启用。
