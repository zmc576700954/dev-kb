"""项目路由：唯一识别才自动限定项目，其余情况必须显式确认。"""

from __future__ import annotations


def matching_projects(question: str, registry: dict) -> list[str]:
    query = question.lower()
    return [name for name in registry if name.lower() in query]


def route_project(question: str, registry: dict, project: str | None = None,
                  all_projects: bool = False) -> dict:
    """返回 ok / project_required / unknown_project，绝不隐式扩大检索范围。"""
    if project and all_projects:
        return {
            "status": "unknown_project",
            "message": "project 与 all_projects 不能同时使用",
            "candidates": sorted(registry),
        }
    if all_projects:
        return {"status": "ok", "project": None, "candidates": []}
    if project:
        if project not in registry:
            return {
                "status": "unknown_project",
                "message": f"未登记的项目: {project}",
                "candidates": sorted(registry),
            }
        return {"status": "ok", "project": project, "candidates": []}
    matches = matching_projects(question, registry)
    if len(matches) == 1:
        return {"status": "ok", "project": matches[0], "candidates": []}
    return {
        "status": "project_required",
        "project": None,
        "candidates": matches or sorted(registry),
        "message": "无法唯一确定项目，请指定 project；确需跨项目搜索时显式设置 all_projects=true",
    }
