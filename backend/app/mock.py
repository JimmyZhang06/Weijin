"""Deterministic, zero-cost fixtures. This module is not an actual LLM."""

from typing import Protocol

from .domain import character_context


class StoryProvider(Protocol):
    def compose(self, state: dict, request: dict, instruction: str) -> dict: ...


class MockProvider:
    def compose(self, state, request, instruction):
        kind = request["kind"]
        a, b = state["characters"][:2]
        if kind == "scene":
            index = len(state["scenes"]) + 1
            alternate = bool(instruction)
            body = (
                f"{a['name']}在站台边停下。{b['name']}已经走到车门前。\n\n“那就到这里吧。”{a['name']}说。\n\n"
            )
            if alternate:
                body += (
                    f"{b['name']}回过头：“你是不是还有话要说？”\n\n"
                    f"{a['name']}沉默了一会儿。“有。但我想慢慢说。”"
                )
            else:
                body += f"{b['name']}点了点头，没有回头。\n\n列车启动了。没有说完的话，留在了站台。"
            plan = next(
                (p for p in state.get("chapter_plans", []) if p["id"] == request.get("plan_id")), None
            )
            return {
                "kind": kind,
                "title": plan["title"] if plan else ("这一次，她回过头" if alternate else "留在站台的话"),
                "body": body,
                "scene_number": index,
                "mock": True,
                "note": "固定车站模板，仅替换人物名；开场与分支指令未被模型理解。",
                "plan_id": request.get("plan_id"),
            }
        scene = next(x for x in state["scenes"] if x["id"] == request["scene_id"])
        cid = request["character_id"]
        ctx = character_context(state, cid, scene["number"])
        person = ctx["character"]
        if kind == "perspective":
            body = f"对{person['name']}来说，那一刻很难开口。\n\n" + scene["body"]
            title = "同一刻，另一面"
        else:
            body = "有些话，当时没有说出口。\n\n我还没有想好，应该怎样表达。"
            title = "日记" if request.get("artifact_type") == "diary" else "未寄出的信"
        return {
            "kind": kind,
            "title": title,
            "body": body,
            "character_id": cid,
            "scene_id": scene["id"],
            "scene_number": scene["number"],
            "mock": True,
            "note": "固定表达模板，不新增世界事实。",
        }
