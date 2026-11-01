# 数据、关系与证据契约

每轮 review.json 为主数据。大型项目可分片，报告前合并去重校验；不能只验证一片就称全项目通过。ID 同轮唯一，跨轮保留对象/问题逻辑 ID，证据版本另存。

schema_version=1.0保持兼容；可选 tasks、rule.required_evidence_checks、evidence.verification/origin_group、checks/issues.object_ids 的增强契约见[工作状态与发布工具](workflow-tools.md)。不自动将历史说明文字转换成已核验字段。

多智能体模式的 `parallel-plan.json` 和各专业 `fragment.json` 是协作中间数据，不是正式主数据。其所有权、ID前缀、基线指纹和合并规则见[多专业智能体并行审核](parallel-agent-review.md)；只有总审合并并通过本契约校验后的 candidate 才可进入图面复核和发布。

## 最低数据集

顶层含 schema_version="1.0"、project、sources、sheets、rules、scopes、evidence、checks、issues；复数字段为对象数组。

| 集合 | 必需字段 |
|---|---|
| project | name、round、expected_scope_basis（预期范围依据） |
| sources | source_id、path（绝对路径）、sha256、read_status（read/partial/unreadable/pending）、limitations（数组） |
| sheets | sheet_id、source_id、drawing_no、revision、status（current/superseded/reference/unresolved）、locator（页码/布局/图框） |
| rules | rule_id、package、title、basis_type（drawing_consistency/project_requirement/normative/engineering_risk）、basis、verified（布尔值） |
| scopes | scope_id、kind（site/building/floor/zone/space/component/system/interface）、label、discipline（数组）、required_depth（L1/L2/L3/L4） |
| evidence | evidence_id、source_id、source_sha256、sheet_id（图面证据填写，非图纸资料为 null）、locator、grade（A/B/C/D）、independence_key |
| checks | check_id、scope_id、rule_id、applicability（yes/no/unknown）、applicability_reason、target_depth、achieved_depth（L0–L4）、result、review_status、evidence_ids、issue_ids |
| issues | issue_id、title、scope_ids、rule_ids、severity（high/medium/low）、judgement、status、finding、impact、question_to_designer、evidence_ids、closure_evidence_ids、recheck_ids |

scope 表示空间/系统/接口，不仅房间。可增加 parent_id、building、floor、axes、system_id、zone、instances、registration_id，保留原始名称；门号或型号不是唯一实例 ID。

rule.basis 保存原文/摘要及来源位置；规范规则另记录官方来源、版本、核实时间和适用条件。verified 是依据已核实，不代表执行通过。

check.result：pending/pass/issue/needs_confirmation/blocked/not_applicable；review_status：pending/reviewed。不适用须 applicability=no、result=not_applicable 并有理由；unknown 不能排除分母。

issue.judgement：confirmed_conflict / verified_noncompliance / confirmed_missing / interface_open / construction_risk / needs_confirmation / undetermined。
issue.status：open / answered_pending_drawing / drawing_updated_pending_review / closed / accepted_risk / duplicate。

## 对象、坐标与关系

关系另存 relations.json 或 relations 扩展字段：relation_id、from_object、to_object、relation_type、system_id、scope_id、evidence_ids、certainty、version_dependencies。关系类型区分 spatial_adjacency、physical_connection、logical_supply、control、support、index_reference、interface_requirement；交叉线、同名、相邻都不是连接证据。

对象身份用 source_hash + sheet_id + 句柄/完整父块实例路径，或 PDF 页码+局部区域+编号；PDF 不编造句柄。跨版本匹配保留依据，不仅凭句柄。

叠合记录单位、轴网匹配、变换、控制点、残差及来源；镜像、旋转、缩放、局部坐标分别处理。容许误差按检查精度说明。标高注明绝对/相对、结构/完成面、管底/中心/顶；保温、支吊架、安装与维修包络分开。精度不足只报候选干涉。

## 证据与判断

保存原文、对象、量值/单位、定位、截图路径、推理。A 为直接排他证据；B 为关联图面/资料支持；C 为候选识别/几何证据；D 为经验风险或资料缺口。

independence_key 描述产生源。同一图面同一标注的截图、OCR、实体提取共享 key，不算独立证据；不能任意造 key 增强可信度。

同一块定义在平面和详图中重复出现时记录其共同来源及实例差异，不仅按sheet_id区分独立性。CAD证据可附 visibility_audit、geometry_file、image、render_limit 等扩展字段，路径须能解析且所载内容与声明范围一致；详细核验见[CAD证据核验](cad-evidence-verification.md)。

确认矛盾、确认缺失、已核实不符合，须一项直接排他 A 证据，或两项独立 B 证据及推理。确认缺失还须 missing_basis（为何应有）和 search_scope（完整检索范围及限制）；搜索不到不等于确认不存在。verified_noncompliance 至少关联已核实的 normative/project_requirement 规则。

source_sha256 固定提取时的来源版本。历史证据可说明原问题，但当前检查通过、当前确认结论及关闭依据应来自当前有效图面。未确定版本、不可解析文件及其读取错误可支持待确认/无法判断事项，不能支持通过或确认错误。变更后使旧检查失效再执行，不只更新哈希。

高影响低证据事项保留 high + needs_confirmation/undetermined；不强行凑证据或降低后果评级。不要求伪精确置信数值。

## 闭环

建议保留 origin_round、revision_history、affected_scopes、responsible_party（建议/确认分开）、reply、due_before。相同根因可归并，每个受影响实例仍记录。

closed 须 closure_basis、closure_evidence_ids、recheck_ids；复核检查 reviewed、pass、达到深度且覆盖全部 scope_ids。回复可直接执行且无需改图时写原因；需要改图时不能仅凭回复关闭，跨专业影响另建检查。

accepted_risk 须 risk_acceptance_basis，单列未消除风险；duplicate 须 duplicate_of 指向保留主问题，二者不等于 closed。
