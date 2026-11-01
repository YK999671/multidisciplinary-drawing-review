# 多专业智能体并行审核

并行模式用于资料量较大、可按专业或明确接口拆分的项目。总审智能体始终保留基线、任务划分、跨专业判断、合并、质量复核和发布责任。专业智能体是隔离的执行者，不直接改正式 `review.json`，不单独发布，也不代表设计专业签认。

小项目、强耦合的单一问题、尚未建立有效版和范围基线的资料，顺序审核通常更稳。并行的收益主要来自专业内部读取和证据整理同步进行；跨专业接口仍须在专业结果汇齐后复核。

## 1. 总审先建立计划

正式分派前，总审完成资料盘点、有效版、预期专业范围、scope、rule 和待执行 check。将计划保存为本轮目录中的 `parallel-plan.json`：

```json
{
  "schema_version": "1.0",
  "run_id": "R8-P1",
  "base_review": "../R7/review.json",
  "base_review_sha256": "实际SHA256",
  "target_round": "R8：多专业并行续审",
  "assignments": [
    {
      "assignment_id": "STR",
      "discipline": "结构",
      "scope_ids": ["B4-FOUNDATION"],
      "rule_ids": ["STR-FOUND-01"],
      "owned_check_ids": ["CHK0187"],
      "id_prefix": "P1-STR-",
      "fragment": "agents/STR/fragment.json",
      "exclusive_resources": ["autocad-session"]
    }
  ]
}
```

- assignment_id、fragment 和 id_prefix 唯一；不同前缀不能相互包含。
- 一个既有 check 只能有一个专业任务拥有。专业智能体可更新自己拥有的 check，不能更改其 scope 或 rule。
- 新增 evidence/check/issue/relation 使用任务 id_prefix，且范围与规则不得越出分派。
- `exclusive_resources` 表示不能同时操作的资源，如同一个 AutoCAD 会话、同一个临时导出目录或单写接口。同批任务不能共享这些资源。
- 基线已使用 tasks 时，每个分派另填现有 `task_id`，新增 check 自动归入该任务。未使用 tasks 的历史台账不为并行而强行迁移。

验证计划并根据实际可用的专业工作槽生成批次：

```text
python <scripts>/parallel_review.py validate-plan <本轮/parallel-plan.json>
python <scripts>/parallel_review.py schedule <本轮/parallel-plan.json> --worker-slots <可用专业智能体数>
```

`worker-slots` 只填写实际可用的专业执行槽，不包含仍在工作的总审。不要固定假设并发数量。排程只处理计划中声明的互斥资源；未声明的应用限制仍由总审控制。

## 2. 分派专业智能体

总审按 schedule 的 wave 启动专业智能体。每个任务提示至少包含：基线台账及哈希、assignment 完整内容、相应专业参考文件、源资料路径、只读要求、已有用户确认、证据契约、成果路径和停止条件。

专业智能体只处理分派的 scope、rule 和 check。发现越界问题时写入 notes，交总审另建接口任务；不能自行扩大已完成范围。共享 AutoCAD 会话等互斥资源按 wave 串行，读取独立文件和离线分析可并行。

每个专业任务输出 `fragment.json`，状态可以是 complete、partial 或 blocked：

```json
{
  "schema_version": "1.0",
  "run_id": "R8-P1",
  "assignment_id": "STR",
  "base_review_sha256": "与计划一致",
  "status": "partial",
  "notes": ["完成承台表与平面编号核对"],
  "limitations": ["墙柱插筋关系尚未回查"],
  "new_evidence": [],
  "new_checks": [],
  "new_issues": [],
  "new_relations": [],
  "check_updates": []
}
```

更新既有 check 时提交完整替换值和原记录指纹：

```json
{
  "check_id": "CHK0187",
  "expected_sha256": "原check规范化JSON的SHA256",
  "value": {"check_id": "CHK0187", "scope_id": "B4-FOUNDATION", "rule_id": "STR-FOUND-01"}
}
```

`value` 必须包含原 check 所需的全部字段。指纹由 `parallel_review.canonical(原check对象)` 计算，避免智能体基于旧记录覆盖新结果。证据附件路径相对 fragment 所在目录；源图仍只读。

## 3. 校验、合并和跨专业复核

专业成果到达后逐项校验：

```text
python <scripts>/parallel_review.py validate-fragment <parallel-plan.json> <assignment_id>
```

校验覆盖任务身份、基线哈希、范围/规则所有权、ID前缀、既有检查指纹、引用和附件存在性。它不验证工程判断是否正确。

全部已到达成果可合并为新的候选目录：

```text
python <scripts>/parallel_review.py merge <parallel-plan.json> --output <本轮/candidate>
```

合并复制基线证据和专业附件，更新被授权的检查，追加新记录，重新核源哈希并运行完整台账校验。输出标记为 `candidate_requires_chief_review_and_qa`；partial 或 blocked 任务可以合并，但不能因此视为专业完成。输出目录已存在时拒绝覆盖，合并失败不产生正式成果。

总审随后执行：

1. 对专业间同一对象、同一根因和共享证据去重，检查是否出现结论冲突。
2. 建立跨专业接口任务，核对结构洞口/荷载/支承、建筑净高与检修、给排水/暖通/电气路径及消防联动等关系。
3. 回查高影响问题和专业智能体标记的限制；专业智能体的 complete 只表示其成果包提交完成。
4. 修订候选台账的项目说明、覆盖分母和未完成范围，重新运行 `validate_review.py`。
5. 按 `workflow-tools.md` 生成 QA、完成实际图面复核，再 publish。不得直接发布 fragment 或未经总审复核的 candidate。

## 4. 中断与失败

- 智能体失败时保留其目录，状态记 blocked 或 partial；总审可重新分派同一 assignment，但新成果仍须绑定同一基线，或整体重建计划。
- 基线台账或其哈希变化后，旧 fragment 不得机械改哈希继续使用；先判断哪些任务失效并重跑。
- 多个智能体报告同一问题时，总审保留一个逻辑问题，其他记录作为证据或受影响实例，不能靠删除记录掩盖范围。
- 每个 wave 和合并后调用 checkpoint，记录已完成 assignment、未结束任务、候选目录和下一步接口复核。
- 软件校验、智能体一致意见和多数票都不能代替原图证据、规则适用性核实或专业计算。
