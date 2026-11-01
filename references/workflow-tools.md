# 工作状态、任务汇总与发布工具

工具位于 scripts/review_workflow.py，使用 Python 标准库。它保存已提供的工作状态，不会在后台自行监控审图；每完成一批实质检查、准备切换任务或中断前调用 checkpoint。不要声称尚未写入的断点已保存。

## 工作目录与事务边界

项目输出根目录保存 workflow.json，它是唯一正式入口指针。指针指向 checkpoints/<revision>/state.json 与 resume.md；每次成功保存产生新快照，旧快照保留。latest_validated 与 work_round 分开。

发布产物在 releases/<唯一ID>/ 中。全部写入并验证后才原子替换 workflow.json；发布失败不改变旧指针，可能留下未发布目录。不能用“最新修改目录”推断正式成果，不自动删除失败目录。单写锁与 expected_revision 防止并发覆盖；遗留锁先核进程，不盲目移除。

以下命令的 `<scripts>` 为本skill脚本目录，`<project>` 为项目输出根目录。首次操作省略 expected-revision；以后先 status，传回其 revision。路径含空格时加引号。

```text
python <scripts>/review_workflow.py status <project>
python <scripts>/review_workflow.py checkpoint <project> --patch <工作状态补丁.json> --expected-revision <revision>
```

补丁只填写要更新的字段；数组为整体替换，未提供字段保留：

```json
{
  "work_round": "R2",
  "current_object": "B1某承台标高与降板接口",
  "baseline_confirmations": ["已确认补充图作为该范围基线"],
  "completed_steps": ["已提取局部标注，尚未完成图面复核"],
  "candidate_findings": ["两个标高的对象对应关系待核"],
  "next_actions": ["回查剖切索引与局部板面"],
  "running_processes": [],
  "work_artifacts": ["R2/trace.json"]
}
```

work_round 和 work_artifacts 必须在项目输出根目录内且存在。工作步骤不自动变成检查通过或进度；latest_validated 不能通过普通补丁篡改。status 验证状态及已登记成果完整性，但不替代每轮源文件哈希和图面复核。

## 综合任务与检查记录

review.json 可选顶层 tasks，用于汇总 checks；不新增一种工程结果，不把任务条数加入完成率。

```json
[
  {"task_id":"T-STR","label":"结构审核","task_ids":["T-FOUND"]},
  {"task_id":"T-FOUND","label":"基础对象核对","check_ids":["CHK0001","CHK0002"]}
]
```

启用 tasks 后，每条检查必须且只能归属一个直接任务；每个子任务最多一个父任务。允许多个根任务。拒绝未知引用、循环、重复归属、空任务及手填 status。状态由子检查推导为 pending、in_progress、blocked、complete、not_applicable；complete 只表示审查完成，不表示问题关闭。

聚合结构使用 tasks，不在 checks 中使用 parent_check_id。历史台账未声明 tasks 时仍可校验；不自动猜测父子关系或删除综合检查。对旧综合项进行正式拆解时，人工保存原目标及替代映射，确认底层检查覆盖剩余工作后再迁移。任务去重只避免重复汇总，不证明不同检查的工程范围没有重叠。

需要按专业调用多个智能体时，使用[多专业智能体并行审核](parallel-agent-review.md)和 `scripts/parallel_review.py`。专业智能体输出隔离 fragment；总审合并后的 candidate 仍是未发布工作轮次，必须继续执行本文件的 QA 与 publish。

## 证据与关闭补强

- 校验器拒绝仅凭C/D候选证据的通过项。
- rule.required_evidence_checks 可声明字符串数组，如 object_identity、visibility、dimension_display、datum_conversion；对应 evidence.verification 保存布尔核验记录。完成检查须有A/B证据明确满足各项；这些值必须来自实际核验，程序不验证其真实性。历史规则未声明时给警告，不擅自补true。
- evidence.origin_group 可表达共享的块定义/原始标注来源；确认问题统计独立B证据时优先按此分组。未填时仍依赖 independence_key，不宣称自动识别同源几何。
- 关闭复核必须覆盖原问题的范围和规则。若问题声明 object_ids，复核检查还须覆盖这些对象；旧问题没有对象字段时保留人工核验警告。
- relations 校验必需字段、证据/源版本/范围引用及ID唯一性；自由文字端点仍不等于已建立可计算拓扑。
- `validate_review.py <review.json> --verify-source-files --verify-artifacts` 同时核源哈希和声明附件是否存在。证据附件必须在本轮目录内；尚未复核或能力不足的证据不能用修改字段绕过校验。

## 发布

先生成与台账及附件哈希绑定的复核模板：

```text
python <scripts>/review_workflow.py prepare-qa <轮次/review.json> --output <轮次/quality-review.json>
```

模板初始为 pending。完成实际图面回查后填写 reviewed_by、evidence_ids_checked、limitations，并改为 reviewed。至少回查所有未关闭的高影响确认问题证据、一个通过项的完整证据；这只是最低程序门槛，按本轮变化和风险扩大复核范围。台账或附件变化后需重新复核并更新绑定，不机械刷新哈希延续旧结论。

```text
python <scripts>/review_workflow.py publish <project> --review <轮次/review.json> --qa <轮次/quality-review.json> --expected-revision <revision>
```

发布复制主台账及声明证据，生成报告、检查清单、问题台账、证据索引、资料清单和任务状态（Markdown/HTML），保存校验、复核和文件哈希清单，再更新入口。源轮次保留；没有被结构化台账引用的额外专项报告不会自动携带，应先登记或保留原轮次链接。自动报告是台账摘要，不能替代专业解释。

## 接入历史成果

```text
python <scripts>/review_workflow.py register-baseline <project> --review <已发布轮次/review.json> --qa <已发布轮次/evidence-qa.json> --expected-revision <revision>
```

仅用于尚未有 latest_validated 的项目；要求已有报告、历史QA valid=true，重新核源哈希、台账及附件。历史QA由操作者确认属于该报告，此命令不声称重新完成工程复核。未发布工作轮次仅登记 checkpoint，不运行此命令冒充正式成果。

## 验证

运行 test_validate_review.py、test_review_workflow.py 和 test_parallel_review.py。测试覆盖任务状态、证据约束、并发版本、专业任务所有权、隔离合并、输入变更及写入失败保留旧入口。它们是软件行为测试，不是工程审图准确率测试。
