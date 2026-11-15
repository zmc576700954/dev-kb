"""知识源契约的核心规则集。

7 条规则，全部**可机械判定**，只针对"检索能不能用"这件事，不评判文字质量、
措辞、篇幅或文档生命周期。判断标准是：

1. **error**：缺失会造成可观测的检索退化，且能机械判定、不会误报；
2. **warning**：值得知道，但不该阻断接入。

每条规则声明 ``applies_to``——依赖哪些可选结构字段。知识源没声明对应字段时，
该规则**不适用**（不是 finding）。这样同一个契约可以套在文档型知识库、
代码仓库和未来任何形态的知识源上。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str          # error | warning
    title: str
    statement: str
    applies_to: tuple[str, ...] = ()   # 空 = 恒适用


RULES: dict[str, Rule] = {
    r.id: r
    for r in [
        Rule(
            id="R-01",
            severity="error",
            title="内容边界必须显式声明",
            statement="知识源必须声明 content_roots（或显式声明全库）；"
                      "未声明的路径不进入索引。",
        ),
        Rule(
            id="R-02",
            severity="error",
            title="声明的结构能力必须真实存在",
            statement="声明 provides 含 heading_path 的知识源，其 document 块"
                      "的 heading_path 非空率须 ≥ 95%；低于阈值该能力自动降级。",
            applies_to=("heading_path",),
        ),
        Rule(
            id="R-03",
            severity="error",
            title="意图映射的目标必须存在",
            statement="intent_map 中每条路径模式在知识源根下必须有实际匹配目标。",
        ),
        Rule(
            id="R-04",
            severity="error",
            title="入口文档必须存在",
            statement="声明了分类结构的知识源必须声明 entry_docs，且每个入口文档真实存在。",
            applies_to=("category",),
        ),
        Rule(
            id="R-05",
            severity="warning",
            title="文档块应有标题链",
            statement="document 块缺 heading_path 时检索可信度下降，应补齐标题层级。",
            applies_to=("heading_path",),
        ),
        Rule(
            id="R-06",
            severity="warning",
            title="术语别名必须有出处",
            statement="aliases 中每个术语都必须在至少一个块的正文里出现，"
                      "否则别名是噪声。",
            applies_to=("aliases",),
        ),
        Rule(
            id="R-07",
            severity="error",
            title="声明不得超出契约字段集",
            statement="provides 中出现的字段名必须属于契约定义的可选结构字段集，"
                      "防止声明腐化。",
        ),
    ]
}


def rule_ids() -> list[str]:
    return list(RULES)


def get_rule(rule_id: str) -> Rule | None:
    return RULES.get(rule_id)
