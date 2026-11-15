"""知识源接入契约：dev-kb 的第二个子系统。

契约不是文档质量评分，而是"新知识源接入检索器时要满足什么"的约定。
一个知识源在 ``projects.yaml`` 里声明它提供哪些结构字段、内容边界在哪、
中文意图对应哪些路径；检索器按声明消费，缺什么即降级，不拒绝接入。

模块边界：

- :mod:`declaration` —— 声明模型（提供 / 边界 / 意图 / 角色 / 术语）
- :mod:`rules`       —— 7 条核心规则
- :mod:`checks`      —— 规则的只读检查实现
- :mod:`service`     —— 只读服务
- :mod:`cli`         —— 人工入口
- :mod:`mcp_server`  —— 只读 MCP stdio 服务
"""

from .declaration import (
    DOC_ROLES,
    OPTIONAL_FIELDS,
    SourceDeclaration,
    get_declaration,
    load_declarations,
)
from .rules import RULES, get_rule, rule_ids
from .service import ContractService

__all__ = [
    "DOC_ROLES",
    "OPTIONAL_FIELDS",
    "RULES",
    "ContractService",
    "SourceDeclaration",
    "get_declaration",
    "get_rule",
    "load_declarations",
    "rule_ids",
]
