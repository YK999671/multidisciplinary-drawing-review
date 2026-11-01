# Multidisciplinary Drawing Review Skill

面向建筑工程整套施工图会审预审的 Codex skill。它以有效版本基线和可追溯证据为核心，组织建筑、结构、给排水、暖通、电气、智能化、精装及适用专项的专业内部检查、系统追踪和跨专业接口复核。

## 主要能力

- 资料清点、有效版本和预期专业范围基线。
- 空间、构件、系统、图面和跨专业接口关系。
- 检查计划、证据等级、问题状态和分层覆盖统计。
- 多专业智能体隔离并行，总审统一校验、合并和复核。
- 长任务断点、不可覆盖的状态快照和安全发布。
- 源文件、证据附件、任务所有权和台账引用校验。

本 skill 用于施工图会审预审，不替代法定施工图审查、设计签认或专业计算。单独精装修审核应使用相应精装 skill；独立市政道路和桥梁工程需要另建专业检查包。

精装修检查包通过可选的 `interior-fitout-drawing-review` skill 接入；该依赖未包含在本仓库中。CAD 深度读取可配合当前环境中的 `cad-spatial-analysis` skill，缺少这些可选能力时应在报告中披露受限范围。

## 安装

将本仓库克隆或复制到 Codex skills 目录，使目录结构保持如下形式：

```text
multidisciplinary-drawing-review/
├── SKILL.md
├── agents/
├── assets/
├── references/
└── scripts/
```

重新打开 Codex 会话后，可直接提出“使用全专业图纸预审 skill 审核某文件夹”。

## 验证

```text
python -X utf8 scripts/test_validate_review.py
python -X utf8 scripts/test_review_workflow.py
python -X utf8 scripts/test_parallel_review.py
```

当前测试覆盖台账契约、证据和关闭条件、状态快照、发布保护、专业任务所有权、资源排程、隔离成果合并及过期基线拒绝。软件测试不证明工程审图结论正确。

详细工作流从 [SKILL.md](SKILL.md) 开始；多智能体并行机制见 [references/parallel-agent-review.md](references/parallel-agent-review.md)。
