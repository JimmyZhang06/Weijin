# 数据模型与领域规则

## 1. 必须分离的概念

1. 世界事实：故事中实际发生或成立的事情。
2. 角色认知：某人在某个故事时刻知道、相信、怀疑或误解的事情。
3. 读者可见内容：在当前阅读进度与剧透偏好下可见的内容。
4. 创作提案：尚未被用户接受的模型生成内容。

人物说“我没收到信”不自动成为世界事实，可能是误解或谎言。日记也不是全知叙述。用户在作者模式可以查看秘密，但角色上下文不能因此获得该秘密。

## 2. 主要实体

所有业务表有 UUID、时间戳。世界内实体带 world_id；查询必须同时校验所有权和关联对象归属。下表为逻辑设计，P0/P1 转为迁移。

| 实体 | 关键字段 / 作用 |
| --- | --- |
| users | 外部认证 subject；本地开发可使用固定测试用户 |
| worlds | owner_id、标题、语言、创作约束、默认分支 |
| characters | world_id、稳定人物 ID；具体设定存入修订快照 |
| branches | world_id、parent_branch_id、fork_revision_id、head_revision_id、version |
| revisions | branch_id、parent_revision_id、kind、不可变 state_snapshot、state_hash、来源命令 |
| scene_versions | revision_id、scene_id、正文、结构化事件、叙事视角、内容 hash |
| facts | revision_id、稳定 fact_id、类型、命题、故事时间、来源、锁定标记 |
| character_knowledge | revision_id、character_id、命题/引用、认知类型、获知时间、来源事件 |
| relationships | revision_id、双方人物、关系描述、依据事件；不生成伪精确心理百分比 |
| secrets | revision_id、fact_id、知情人物、揭示条件、是否已进入正文 |
| artifacts | base_revision_id、场景锚点、人物、类型、正文、事实依赖、审核状态 |
| artifact_acceptances | revision_id、artifact_id；档案被纳入故事的不可变关联 |
| reader_progress | user_id、branch_id、阅读位置、已解锁 artifact_id |
| drafts | job_id、base_revision_id、正文/档案、proposed_delta、校验报告、状态 |
| jobs | owner_id、branch_id、类型、base_revision_id、状态、租约、预算、取消标记 |
| job_steps | job_id、node_key、input_hash、状态、结果、prompt/model 版本 |
| job_events | job_id、递增 event_id、事件类型、经裁剪的 payload |
| usage_entries | job_id、调用 ID、模型、token、估算/实际成本、未知用量标记 |
| quota_reservations | user_id、job_id、预留量、结算量、状态 |
| command_receipts | owner_id、operation、idempotency_key、请求 hash、响应引用 |

事实等结构化投影和 revisions.state_snapshot 在同一事务内写入。快照为权威内容，投影可以重建；检测到 hash 不匹配时停止生成并报告，不静默选一个版本继续。

## 3. 正史修订

revision 是不可变的正史检查点，可包含 scene、artifact_accept、setting_edit 或 fork_override 变更。首版对少量人物采用完整结构化快照，优先保证正确性；正文按不可变版本存储。

状态至少包含：人物设定、锁定项、已发生事件、世界事实、角色认知、关系、秘密、场景顺序、已接受档案引用。每个事实和认知必须能追踪至作者设定或已接受事件。

锁定事实更改必须来自显式用户命令，产生新修订并提示影响范围。生成节点没有解除锁定的权限。

## 4. 故事时间与写入时间

数据库 created_at 只用于审计，不能当作故事时间。事件使用场景序号、场景内顺序和可选世界时间。角色在 t 时刻的上下文，只包含其在 t 之前已获得的认知；不读取当前最新认知去生成过去番外。

认知示例：

```json
{
  "character_id": "linzhou",
  "proposition": "许岚主动拒收了信",
  "epistemic_type": "belief",
  "acquired_at": {"scene": 2, "order": 4},
  "source_event_id": "ev-rumor-01"
}
```

这是角色的信念；真实事实可能是信被第三人截走。对角色模型暴露 belief，但不得附上它不知道的真实答案。

## 5. 私密档案与多视角

首版档案类型：日记、未寄出的信。另一视角为独立场景文本，仍锚定原场景及其发生时的状态。

- 允许表达已存在的感受、认知和已确认的幕后行为。
- 不允许凭空新增“凶手”“关键证据”“改变因果的幕后事件”。发现需要此类新增时返回 needs_input，请用户先确认设定，再重跑。
- 既定秘密先存在于状态中，解锁只是披露；不能点击时临时发明决定性秘密。
- artifact 的生成、接受、读者解锁分别记录。接受可以改变正史版本，解锁只改变读者进度。
- 每个 artifact 保存 base_revision_id 和内容 hash。同一内容重复解锁返回原结果，不重新生成和收费。
- 改写后依赖事实失效的 artifact 标记 stale，在新分支读者视图中隐藏；原分支历史不删除。
- 剧透关闭时，尚未解锁档案的正文、标题提示、事件摘要都不能通过 API/SSE 偷渡。标题使用中性类型描述。

## 6. 分支算法

1. 用户选择已接受的场景边界和改动指令，提交 expected_revision_id。
2. 验证该修订属于当前世界、目标分支的可达历史，复制该点结构化快照创建新分支。
3. 过去的不可变正文可以引用共享；继承范围不得超过 fork_revision_id。
4. 对于“改写场景内部一句话”，实际从该场景开始前的检查点分支，该场景及之后重新生成；响应明确实际分支点。
5. 改动作为创作指令保存，经过提案与接受流程进入新分支；不篡改父分支。
6. 后续读写只解析新分支历史。禁止从父分支最新状态、全局摘要或其他分支检索补充信息。
7. 分支不存在自动合并。用户选择默认分支仅改变入口，不删除其他分支。

## 7. 编辑和并发

头部正文的纯文本修改也会产生草稿和事实差异检查；不得把用户正文直接存入旧修订。修改历史场景采用分支语义。

接受草稿事务必须：锁定 branch → 检查 expected_revision/base_revision 等于 head → 确认任务有效、报告通过 → 写修订、正文/档案及投影 → 条件更新 head 和 version → 写命令回执。任何一步失败整体回滚。

多个任务可基于相同快照生成，但只能先接受一个；其余返回 REVISION_CONFLICT，旧草稿保留，可创建分支或重新生成，禁止自动覆盖。
