# 未尽 · 写作室

文字故事、角色私密档案、多视角与剧情分支的本地开发版本。

**当前是本地开发版。** 已实现可配置 OpenAI API 的创作对话与稿件生成，但尚未填写真实凭据、未进行真实模型调用或中文写作质量评估。原有“模板”按钮继续使用固定模板。

## 当前能做什么

- 创建故事及两个人物，保存设定与仅各自知道的秘密。
- 原创/同人档案、原作锚点、关系、AU、禁改边界与逐章章纲，保存产生独立版本。
- 手写正文、保存及编辑草稿、刷新恢复、显式定稿；修订末章保留历史并标记关联旁页需复核。
- 写作工作台展示实际人物认知、结构检查、任务执行记录与版本历史。
- 通过创作对话讨论剧情、起草本章或修改现有稿件；可引用选中文字，生成独立稿件并对照原文。此功能需要配置 OpenAI。
- 独立 worker 生成草稿；接受后才进入正文，拒绝不改变故事。
- 生成并接受日记、未寄出的信和另一视角，阅读对应正文后解锁。
- 从场景开始前建立新路线，保留父路线，切换查看和导出正文。
- PostgreSQL 存储、不可变修订、版本冲突保护、幂等命令、任务租约和恢复。
- 本机网页界面、任务轮询；后端另提供 SSE 接口。

## 环境与首次安装（Windows PowerShell）

### 接入 OpenAI

在 `backend/.env` 填写 `MODEL_PROVIDER=openai`、`OPENAI_API_KEY`、`OPENAI_MODEL`，然后重启 API 和 worker。本机已准备空配置；首次安装可复制 `backend/.env.example`。模型须支持 Responses API，起稿/改稿还需支持 Structured Outputs。密钥只放本机，不要提交到仓库或聊天中。

默认每次最多输出 4096 tokens，120 秒超时，可配置。首次发送会向 OpenAI 提交作品上下文并可能产生费用；没有配置时发送按钮禁用。详细实现及限制见 [M2 计划](plan/10-conversation-studio.md)。

需要 Docker Desktop（Linux containers）、Python 3.12、uv。默认 API 8765、数据库 55438 均仅绑定 127.0.0.1。若端口占用，先检查使用者，不终止其他项目。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv==0.12.12
Set-Location backend
$env:UV_PROJECT_ENVIRONMENT = '..\.venv'
..\.venv\Scripts\uv.exe sync --frozen
Set-Location ..
```

本次开发已完成虚拟环境和依赖安装。若 `python` 是 Windows 应用商店占位程序，改用已安装的 Python 3.12 完整路径运行第一条命令。

## 启动

终端一（API，Ctrl+C 停止）：

```powershell
.\scripts\start.ps1
```

终端二（worker，Ctrl+C 停止）：

```powershell
.\scripts\worker.ps1
```

打开 [本地故事工坊](http://127.0.0.1:8765)。API 契约可读取 [OpenAPI JSON](http://127.0.0.1:8765/openapi.json)。由于严格本地 CSP，默认外部 CDN Swagger UI 不作为演示入口。

`docker compose stop db` 可停止数据库，数据保留在项目专属 volume。不要使用 `down -v`，它会删除故事数据。

本轮可能已在后台启动 API/worker；查看 `artifacts/processes.json` 与日志。启动第二份前先检查进程，后台进程随系统重启结束。后续优先使用上述两个终端脚本。

## 验证

```powershell
.\scripts\test.ps1
npm ci --ignore-scripts
npm run check
```

测试脚本自动创建独立 `weijin_test` 数据库并应用迁移，不清空开发故事、不调用模型。测试使用随机测试用户隔离；测试数据保留以便排查。不要让额外 worker 指向测试库。

## 结构

```text
plan/       产品、后端、前端设计及实施进展
backend/    FastAPI、领域、mock、worker、Alembic、pytest、uv.lock
frontend/   原生 HTML/CSS/JS，同源流程原型
scripts/    启动与测试
compose.yaml  项目专属 PostgreSQL
```

当前前端选择无打包依赖的原生模块，目的是验证交互与接口。npm 仅用于锁定的 Prettier 格式检查，服务运行不依赖 Node。完整前端架构、路由、编辑器、设计系统将在后续评估，不把原型当成最终工程结构。

## 明确未完成

真实模型调用验收与生成质量、金额预算/计费、事实变更提案与语义审校、富文本与批注编辑、人物增删、定时连载、生产认证、删除与备份恢复、生产容器部署均未完成。现已支持既有人物编辑、基础 OpenAI 适配、调用续租、取消和用量记录。

事实与认知当前保存在不可变 JSONB 快照中，尚未建立独立投影表。当前角色上下文不读取完整故事正文，尚需增加经过知识边界筛选的观察事件。禁止直接接入真实模型并宣称人物一致性已解决。

本地固定身份只适用于可信本机，服务拒绝非本地请求并阻止生产模式启动。请勿通过公网代理、端口转发或共享隧道暴露此版本。

最新状态与验收见 [对话式工作台](plan/10-conversation-studio.md)，早期状态见 [写作工作台整改](plan/09-writing-workspace.md) 和 [实施进展](plan/08-implementation-status.md)。

## M3：全 AI 写作与新版写作室

打开主应用 `/`，进入任意作品后点击「全 AI 写作」。先保存作品设定，再填写本轮创作要求，选择章节数和每章目标字数。后台依次规划章纲、逐章生成初稿，可暂停后续章节、继续或立即停止，完成稿可阅读、导出 Markdown，并按顺序送入编辑器审阅 / 定稿。

本轮最多 8 章、9 次模型调用，失败后停止；字数为创作目标。初稿不自动进入正文。OpenAI 配置继续使用 `backend/.env`，需要同时配置 `OPENAI_API_KEY` 与 `OPENAI_MODEL`，重启 API 和 worker 后生效。缺少配置时开始按钮禁用。测试不消耗真实模型额度。

实际工作台新增浅深主题、字号调整、专注模式、章纲意图和撤销未保存修改。完整范围与约束见 `plan/11-autowrite-redesign.md`。

## 当前默认服务：DeepSeek

模型服务已切换为 DeepSeek。编辑本机 `backend/.env`：

```dotenv
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=在本机填写密钥
DEEPSEEK_MODEL=deepseek-v4-flash
```

填写后重启 API 与 worker。对话、改稿、章纲及全 AI 写作均使用同一配置。既有 OpenAI 配置仅用于显式选择 OpenAI 或历史任务。密钥不能发到聊天或提交到代码库。接口差异及约束见 `plan/12-deepseek-provider.md`。
