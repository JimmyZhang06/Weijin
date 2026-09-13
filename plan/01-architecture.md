# 技术架构与选型

## 1. 系统组成

```text
前端：阅读、档案解锁、视角切换、分支、正文编辑
    │ HTTP 命令 / SSE 进度
FastAPI
    ├── 身份认证与对象权限
    ├── 世界、人物、分支与正史服务
    ├── 草稿审阅和原子接受服务
    ├── 任务创建、取消与事件读取
    └── 导出服务
    │
PostgreSQL：业务状态 + 不可变修订 + 任务 + 节点结果 + 用量
    │ 租约领取任务
Worker
    ├── 上下文构建器
    ├── 有界生成状态机
    ├── 导演 / 角色 / 作者 / 连贯性审校
    ├── 确定性规则校验
    └── 模型适配器 → 外部模型服务
```

API 不等待完整生成。创建任务返回 202，worker 生成并持久化结果；客户端断开不会取消任务。PostgreSQL 是故事业务状态的唯一权威来源。

## 2. 技术选择

| 层 | 首版选择 | 原因 / 限制 |
| --- | --- | --- |
| 语言 | Python 3.12 | 类型化领域模型与模型生态；版本是起始候选 |
| API | FastAPI + Pydantic 2 | 请求校验、OpenAPI、类型化结构输出 |
| 数据访问 | SQLAlchemy 2 + Alembic | 事务、迁移和唯一约束显式管理 |
| 数据库 | PostgreSQL | 分支快照、JSONB、任务领取和并发提交统一处理 |
| 任务运行 | 自有小型 worker 状态机 | 只实现本产品的有限步骤；租约、取消、恢复必须先测试 |
| 模型接口 | 自有窄接口 + 单供应商适配器起步 | 避免把模型 SDK 数据结构带入领域层 |
| 流式事件 | HTTP SSE | 推送任务阶段与草稿预览；取消等命令走 HTTP |
| 测试 | pytest + 真实 PostgreSQL 集成测试 | SQLite 不能替代锁、租约与隔离相关验证 |
| 本地运行 | Docker Compose 中的 PostgreSQL；API/worker 可用 Linux 容器 | Windows 工作区通过 Docker Desktop/WSL2 运行；P0 检查可用性 |

使用 uv 管理项目与锁文件；工具可用性在 P0 检查。生产以 Linux 容器为基线。首版文本存数据库；不为了几份文字导出部署对象存储。

## 3. 为什么暂不使用 LangGraph 或 Celery

核心难点是故事正史和权限语义，图编排框架不会自动解决。首版步骤固定，自有状态机更方便明确失败、预算和提交边界。代价是必须实现并验证任务恢复，不能把后台线程当作可靠队列。

LangGraph 提供检查点和持久化能力，可在后续流程分支复杂时引入；即使引入，框架检查点也不等于故事分支。Celery 可用于后续队列扩展，但消息重投仍要求业务幂等。只有容量测量显示瓶颈或流程复杂度明显增加时才提出迁移，避免同时维护两套任务恢复语义。

FastAPI BackgroundTasks 不承载本产品的长时间生成任务。

## 4. 建议目录（后续创建）

```text
backend/
  pyproject.toml
  uv.lock
  app/
    api/                 # 路由、认证、请求响应、SSE
    domain/              # 世界、修订、事实、知识、分支规则
    services/            # 创建任务、接受草稿、分支、揭示档案
    workflows/           # 显式状态机与节点执行
    context/             # 角色上下文和读者投影
    llm/                 # 协议、能力描述、供应商适配、mock
    persistence/         # ORM、事务、任务租约
    worker/              # 领取、心跳、恢复、取消
    observability/       # 结构化日志、用量、指标
  migrations/
  tests/{unit,integration,evals}/
  fixtures/              # 自建原创、可复现故事样本
```

## 5. 模型接口

统一暴露 `generate_structured(schema, context, limits)` 与 `generate_text(context, limits, on_chunk)`，返回文本/对象、结束原因、供应商请求 ID、实际用量或估算标记。

每个适配器声明：结构化输出、流式输出、上下文上限、用量报告、超时与取消支持情况。不能假定所有兼容接口实现相同能力。模型名称、参数、单价表和 prompt 版本进入运行快照。

JSON 不合法时最多修复一次。修复、重试都消耗同一任务预算。模型可以提出结构化变更，但不能直接访问数据库、执行代码、访问网络或修改正史。

## 6. 任务可靠性

- `jobs` 是持久化队列，worker 使用短事务和 `FOR UPDATE SKIP LOCKED` 领取已到期任务；数据库事务不能跨越模型网络调用。
- 领取时递增 fencing token，设置 lease_until；心跳续租。所有节点结果、状态和事件写入检查 token，过期 worker 的结果拒绝写入。
- 节点结果唯一键为 `(job_id, node_key, input_hash)`；重启后复用成功结果，恢复到第一个未完成节点。
- 语义是至少一次执行，不承诺外部模型调用恰好一次。若供应商已完成但响应未落库，可能产生重复费用，必须记录 `usage_uncertain` 并保留预算。
- 租约过期后以新 token 接管；按退避间隔恢复。数据库可达性恢复前，worker 停止启动新调用。
- 任务创建与额度预留在一个事务内完成。由于任务直接在数据库中，无需双写外部队列；未来接入队列时增加 outbox。
- 每个任务每次最多运行一个工作流；其中角色提案允许最多 2 路并发，结果按稳定顺序汇总。
- API/worker 重启和数据库恢复必须有集成测试；不能靠“重试”掩盖不完整的正史提交。

## 7. 官方依据与版本说明

以下链接于 2026-09-10 查阅，支持选型背景，不意味着本计划已完成兼容性验证：

- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)：后台任务用法与外部任务工具的适用讨论。
- [PostgreSQL 事务隔离](https://www.postgresql.org/docs/current/transaction-iso.html)：并发读写必须由事务及条件更新保护。
- [Celery Tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html)：任务幂等、确认、重投与超时。
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：检查点与长期存储各自作用。

具体 SQL、驱动和依赖版本在实现时核验官方文档，并写入锁文件，禁止仅使用浮动 latest 镜像或未锁定依赖交付。
