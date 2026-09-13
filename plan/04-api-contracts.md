# API 与事件契约

基础路径 `/api/v1`，JSON 使用 snake_case，时间使用 UTC ISO 8601，ID 为 UUID。本文件为目标契约；当前已实现子集以 `/openapi.json` 和 `08-implementation-status.md` 为准。

## 1. 身份与访问

所有接口验证登录与对象归属。首版本地模式可用固定开发身份；该模式只能绑定 loopback，生产配置必须拒绝启动。生产推荐同源 HttpOnly 安全会话 Cookie，写操作校验 CSRF 与 Origin；不自建密码系统。外部身份提供方由部署阶段决定。

读者/作者是服务端投影模式，不等同于两个账号角色，也不能绕过所有权检查。首版无公开分享、无多人编辑。无权对象统一 404，避免泄露对象存在性。

## 2. 主要接口

| 方法与路径 | 用途 / 关键契约 |
| --- | --- |
| POST /worlds | 创建世界和主分支 |
| GET /worlds | 分页列出自己的世界 |
| GET /worlds/{id} | 返回世界概览，不附全部秘密 |
| POST /worlds/{id}/characters | 创建人物设定草稿，指定 branch 与 expected_revision |
| POST /branches/{id}/setting-drafts | 修改人物/世界设定，返回差异；接受后形成修订 |
| GET /branches/{id}/state | mode=author/reader；reader 按阅读位置裁剪，author 显式开启剧透 |
| GET /branches/{id}/scenes | 游标分页，只读取当前分支可达正文 |
| POST /branches/{id}/jobs | 创建正文、档案或另一视角生成任务；返回 202 |
| GET /jobs/{id} | 状态、进度、用量估算、草稿引用、可恢复错误 |
| GET /jobs/{id}/events | SSE，支持 Last-Event-ID 重连 |
| POST /jobs/{id}/cancel | 幂等取消；已结束返回当前状态 |
| GET /drafts/{id} | 读取正文、proposed_delta、校验报告 |
| POST /drafts/{id}/accept | expected_revision_id 必填；原子接受，冲突 409 |
| POST /drafts/{id}/reject | 保留审计，草稿标记 rejected |
| POST /scenes/{id}/edit-drafts | 用户编辑正文，生成差异审查草稿；历史场景要求新分支 |
| POST /branches/{id}/forks | 从指定场景边界创建分支，返回实际锚点和新分支 ID |
| GET /branches/{id}/artifacts | 返回可见中性元数据，隐藏未解锁正文 |
| POST /artifacts/{id}/reveal | 校验所属分支、已接受状态和阅读条件；只揭示已生成内容 |
| PUT /branches/{id}/reading-progress | 更新阅读位置，不触发模型调用 |
| GET /branches/{id}/export | 首版 Markdown，默认不包含秘密；作者显式 include_private 才包含 |
| DELETE /worlds/{id} | 取消运行任务，立即隐藏数据，进入异步清理 |

首版创建人物/设定时允许字段表单输入，不要求先上传文件。每个场景 ID 的查询必须结合分支和修订，不能单凭可猜测 ID 读历史或别的分支。

## 3. 创建生成任务示例

请求头 `Idempotency-Key: <客户端随机 UUID>`。

```json
{
  "kind": "private_artifact",
  "base_revision_id": "<uuid>",
  "scene_id": "<uuid>",
  "character_id": "<uuid>",
  "artifact_type": "unsent_letter",
  "instruction": "表达他没说出口的遗憾，不新增幕后事件"
}
```

服务端按 kind 使用判别联合 schema；拒绝无关或未知字段。模型与预算使用服务端允许配置，客户端不能绕过硬上限。

```json
{
  "job_id": "<uuid>",
  "status": "queued",
  "base_revision_id": "<uuid>",
  "events_url": "/api/v1/jobs/<uuid>/events"
}
```

同用户、同操作、同幂等键、同请求 hash 返回原响应；同键不同 payload 返回 IDEMPOTENCY_CONFLICT。任务、fork、accept、setting command 均执行同一规则。幂等回执至少保存到关联资源删除；客户端重试不得创建第二次收费任务。

## 4. SSE

持久化事件示例：

```text
id: 17
event: job.stage_changed
data: {"job_id":"...","stage":"consistency_review","status":"running"}

id: 18
event: draft.ready
data: {"job_id":"...","draft_id":"...","base_revision_id":"..."}
```

事件类型：job.queued、job.stage_changed、draft.preview（作者模式）、draft.ready、job.needs_input、job.failed、job.cancelled、job.budget_exceeded。

- 持久事件按 job 内递增 ID 排序，至少一次投递；客户端按 ID 去重。
- SSE 连接初始验证身份，持续连接在会话失效/世界删除后关闭；不在 URL 放长期令牌。
- 15 秒心跳；代理关闭缓冲。API 初版可每秒查询一次新事件，生产并发增长再优化通知机制。
- 预览分块合并后持久化，不能逐 token 写数据库。最终正文是读取草稿接口的版本。
- 事件保留默认 7 天；Last-Event-ID 超出保留区间返回 410 EVENT_HISTORY_EXPIRED，客户端通过 GET job + draft 恢复。
- 断开流不等于取消；刷新页面必须可以重新找到当前任务。

## 5. 错误格式

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "该分支已有新版本，草稿已保留。",
    "retryable": false,
    "request_id": "<uuid>",
    "details": {"current_revision_id": "<uuid>"}
  }
}
```

主要错误：401 UNAUTHENTICATED；404 NOT_FOUND；409 REVISION_CONFLICT / IDEMPOTENCY_CONFLICT；422 LOCKED_FACT_CONFLICT / KNOWLEDGE_VIOLATION / INVALID_ANCHOR / CONTEXT_TOO_LARGE；429 QUOTA_EXCEEDED / CONCURRENCY_LIMIT；503 MODEL_UNAVAILABLE。

异步任务开始后的失败保存在 job.error，不能伪装成原 POST 的 HTTP 错误。错误响应不返回原始提示词、密钥、供应商内部响应全文或未解锁内容。
