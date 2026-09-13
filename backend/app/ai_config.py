"""Server-side configuration. No secrets are returned by public_status()."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


def settings(provider=None):
    provider = provider or os.getenv("MODEL_PROVIDER", "deepseek")
    prefix = "DEEPSEEK" if provider == "deepseek" else "OPENAI"
    return {
        "provider": provider,
        "model": os.getenv(prefix + "_MODEL", "deepseek-v4-flash" if provider == "deepseek" else "").strip(),
        "key": os.getenv(prefix + "_API_KEY", "").strip(),
        "max_output_tokens": max(256, min(int(os.getenv("AI_MAX_OUTPUT_TOKENS", "4096")), 12000)),
        "timeout": max(10, min(int(os.getenv("AI_TIMEOUT_SECONDS", "120")), 300)),
    }


def public_status():
    cfg = settings()
    ready = cfg["provider"] in {"openai", "deepseek"} and bool(cfg["key"] and cfg["model"])
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "configured": ready,
        "max_output_tokens": cfg["max_output_tokens"],
        "timeout_seconds": cfg["timeout"],
        "message": f"模型已配置；调用时向 {cfg['provider']} 发送所选作品上下文。"
        if ready
        else "在 backend/.env 配置 DEEPSEEK_API_KEY 和 DEEPSEEK_MODEL，重启 API 与 worker。"
        if cfg["provider"] == "deepseek"
        else "在 backend/.env 配置对应服务商的密钥与模型，重启 API 与 worker。",
    }
