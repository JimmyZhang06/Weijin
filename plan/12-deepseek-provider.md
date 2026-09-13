# DeepSeek 模型服务切换

本机默认 MODEL_PROVIDER=deepseek，模型 DEEPSEEK_MODEL=deepseek-v4-flash（可配置），密钥只从 DEEPSEEK_API_KEY 读取。旧 OpenAI 配置保留供历史任务使用，不将旧密钥发给 DeepSeek。

- 新增 deepseek_provider.py，固定官方 https://api.deepseek.com，使用流式 Chat Completions；正文模式关闭 thinking，读取 content，不向作者展示 reasoning_content。
- 讨论流式回复；写作 / 改稿 / 章纲采用 json_object 输出，服务端继续严格验证字段、标题、正文、章纲数量和选段替换。JSON 模式不等同于供应商严格 schema 保证。
- 父任务冻结 provider，章节任务继承，来源与执行记录显示实际服务商。保留幂等、租约、取消、调用上限及人工定稿。
- 流式统计映射 prompt_tokens/completion_tokens 为内部 input_tokens/output_tokens，同时保留原始统计字段。
- 本机 .env 已切换服务商，不写入或展示密钥。填写 DEEPSEEK_API_KEY 后重启 API 与 worker 才能真实生成。
- 官方参考：https://api-docs.deepseek.com/api/create-chat-completion/ 与 https://api-docs.deepseek.com/guides/json_mode/ 。
