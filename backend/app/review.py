def structure_review(content, state):
    """Deterministic checks, explicitly not a semantic/canon/OOC assessment."""
    checks = [
        {
            "name": "标题与正文完整",
            "passed": bool(content.get("title", "").strip() and content.get("body", "").strip()),
        },
        {"name": "正文长度在允许范围", "passed": len(content.get("body", "")) <= 50000},
    ]
    if content.get("plan_id"):
        checks.append(
            {
                "name": "章纲属于当前版本",
                "passed": any(p["id"] == content["plan_id"] for p in state.get("chapter_plans", [])),
            }
        )
    if content.get("kind") in {"private_artifact", "perspective"}:
        checks.extend(
            [
                {
                    "name": "引用场景存在",
                    "passed": any(x["id"] == content.get("scene_id") for x in state["scenes"]),
                },
                {
                    "name": "人物属于当前作品",
                    "passed": any(x["id"] == content.get("character_id") for x in state["characters"]),
                },
            ]
        )
    return {
        "passed": all(x["passed"] for x in checks),
        "checks": checks,
        "scope": "structure_only",
        "semantic_review": "not_performed",
        "notice": "仅检查结构与引用；人物性格、原作一致性及新增事实仍需作者核对。",
    }
