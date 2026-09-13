"""DeepSeek Chat Completions, bounded JSON validation and streamed public content."""

import time

from openai import OpenAI

from .ai_config import settings
from .domain import Problem
from .openai_provider import INSTRUCTIONS, build_input, parse_output


def generate(state, request, instruction, on_delta, record_usage, register_client, stopped):
    cfg = settings("deepseek")
    if not cfg["key"]:
        raise Problem("AI_NOT_CONFIGURED", "worker 未配置 DeepSeek 密钥。")
    system = INSTRUCTIONS
    if request["mode"] == "outline":
        system += '\n规划准确的指定章节数，只返回 JSON 对象，例如：{"chapters":[{"title":"重逢","summary":"交代本章目标、冲突与转折。"}]}。'
    elif request["mode"] != "discuss":
        system += '\n只返回 JSON 对象，必须且仅有三个字符串字段，例如：{"message":"本次创作说明","title":"章节标题","body":"完整正文"}。'
    messages = [{"role": "system", "content": system}, *build_input(state, request, instruction)]
    client = OpenAI(
        api_key=cfg["key"], base_url="https://api.deepseek.com", timeout=request["timeout"], max_retries=0
    )
    register_client(client)
    args = dict(
        model=request["model"],
        messages=messages,
        stream=True,
        max_tokens=request["max_output_tokens"],
        stream_options={"include_usage": True},
        extra_body={"thinking": {"type": "disabled"}},
    )
    if request["mode"] != "discuss":
        args["response_format"] = {"type": "json_object"}
    output, pending, finish, flushed = "", "", None, time.monotonic()
    try:
        if stopped():
            raise Problem("AI_INTERRUPTED", "任务已停止。")
        with client.chat.completions.create(**args) as stream:
            for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage.model_dump()
                    record_usage(
                        chunk.id,
                        dict(
                            usage,
                            input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0),
                        ),
                    )
                if stopped():
                    raise Problem("AI_INTERRUPTED", "任务已停止或超时。")
                for choice in chunk.choices:
                    if choice.finish_reason:
                        finish = choice.finish_reason
                    # Never expose reasoning_content as the author's reply.
                    text = choice.delta.content or ""
                    output += text
                    if len(output) > 60000:
                        raise Problem("OUTPUT_TOO_LARGE", "模型返回内容过长。")
                    if request["mode"] == "discuss":
                        pending += text
                        if time.monotonic() - flushed > 0.4 or len(pending) > 240:
                            on_delta(pending)
                            pending, flushed = "", time.monotonic()
        if finish != "stop" or not output.strip():
            raise Problem("MODEL_INCOMPLETE", "回复未完整生成，已保留原稿并停止后续调用。")
        if pending:
            on_delta(pending)
        if request["mode"] == "discuss":
            return {"message": output, "title": "", "body": ""}
        return parse_output(output, request)
    finally:
        client.close()
