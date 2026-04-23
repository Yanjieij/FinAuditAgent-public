"""语义层：Cube.dev 风格的 measures/dimensions 定义。

**为什么要有语义层**（面试必讲）：

    金融公司最常见的事故是 **"口径分歧"**。
    比如"毛利率"，会计说 ``(revenue - cost_of_goods) / revenue``，
    财务可能口径是 ``(revenue - cost_of_goods - discount) / revenue_net_of_tax``。

    如果让 LLM 从 schema 字段"猜"公式，它每次生成的口径可能都不一样——
    今天报告 15.3%，明天报 14.8%，业务方会爆炸。

    语义层把**指标定义下沉到一个 YAML**，LLM 只能引用 measure 名字，计算公式
    由语义层渲染进最终 SQL。这样口径**代码化 + 代码评审 + 单元测试**，消除分歧。

---

**本项目的简化实现**：
    - YAML 里定义每个 measure 的 SQL 表达式 + 依赖表
    - :func:`render_measure` 把 ``{{ measure:销售费用率 }}`` 占位符展开成具体 SQL

**完整生产方案**：
    - Cube.dev（JS + 独立网关）、dbt 的 metrics layer、或 LookML
    - LLM 生成的 SQL 只允许引用 measures，AST 层检测裸表直写
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # environment.yml 里装了 pyyaml；万一没装给出清晰报错


@dataclass
class Measure:
    """一个业务指标定义。

    Attributes:
        name:     中文名（LLM 引用的就是它），如 "销售费用率"
        sql:      SQL 表达式，可带 {{ dim:xxx }} 占位符
        depends:  依赖的表名，用来和 Schema Linking 交叉校验
        owner:    业主（DBA/财务部），用于审核
    """

    name: str
    sql: str
    depends: list[str]
    owner: str = ""


@dataclass
class Dimension:
    """一个切分维度。"""

    name: str
    sql: str  # 比如 "dim_department.name"
    depends: list[str]


class SemanticLayer:
    """内存里的 measure/dimension 注册表。"""

    def __init__(self):
        self.measures: dict[str, Measure] = {}
        self.dimensions: dict[str, Dimension] = {}

    # ---------- 加载 ----------
    @classmethod
    def from_yaml(cls, path: str | Path) -> "SemanticLayer":
        if yaml is None:
            raise RuntimeError("pyyaml 未安装，无法加载语义层 YAML。")
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        sl = cls()
        for m in data.get("measures", []):
            sl.measures[m["name"]] = Measure(**m)
        for d in data.get("dimensions", []):
            sl.dimensions[d["name"]] = Dimension(**d)
        return sl

    @classmethod
    def demo(cls) -> "SemanticLayer":
        """一个给 examples / tests 用的玩具语义层。"""
        sl = cls()
        sl.measures["销售费用率"] = Measure(
            name="销售费用率",
            sql="SUM(CASE WHEN fact_expense.category='营销' THEN fact_expense.amount ELSE 0 END) "
                "/ NULLIF(SUM(fact_revenue.amount), 0)",
            depends=["fact_expense", "fact_revenue"],
            owner="finance_team",
        )
        sl.measures["部门超支金额"] = Measure(
            name="部门超支金额",
            sql="SUM(fact_expense.amount) - COALESCE(dim_department.budget, 0)",
            depends=["fact_expense", "dim_department"],
            owner="finance_team",
        )
        sl.dimensions["部门"] = Dimension(
            name="部门",
            sql="dim_department.name",
            depends=["dim_department"],
        )
        sl.dimensions["月份"] = Dimension(
            name="月份",
            sql="DATE_TRUNC('month', fact_expense.occurred_at)",
            depends=["fact_expense"],
        )
        return sl

    # ---------- 渲染 ----------
    def render(self, sql_with_placeholders: str) -> str:
        """展开 ``{{ measure:销售费用率 }}`` / ``{{ dim:部门 }}`` 占位符。"""

        def _replace(m: re.Match) -> str:
            kind, name = m.group(1), m.group(2).strip()
            if kind == "measure":
                if name not in self.measures:
                    raise KeyError(f"未知 measure: {name}（请在语义层 YAML 中先定义）")
                return f"({self.measures[name].sql})"
            if kind == "dim":
                if name not in self.dimensions:
                    raise KeyError(f"未知 dimension: {name}")
                return self.dimensions[name].sql
            raise ValueError(f"未知占位符类型: {kind}")

        return re.sub(r"{{\s*(measure|dim):([^}]+?)\s*}}", _replace, sql_with_placeholders)

    # ---------- 给 prompt 用 ----------
    def to_prompt_block(self) -> str:
        """LLM 看得懂的 measure/dimension 清单。只暴露名字和一句描述，不暴露 SQL——
        避免 LLM 自以为聪明去改公式。"""
        lines = ["## 可用的业务指标（只能引用名字，不要自己拼 SQL）"]
        for m in self.measures.values():
            lines.append(f'- measure "{m.name}" 依赖表: {", ".join(m.depends)}')
        for d in self.dimensions.values():
            lines.append(f'- dim "{d.name}" 依赖表: {", ".join(d.depends)}')
        lines.append("")
        lines.append("## 引用语法")
        lines.append('使用 `{{ measure:销售费用率 }}` 和 `{{ dim:部门 }}` 占位符，')
        lines.append("真实 SQL 表达式会由语义层在执行前展开。")
        return "\n".join(lines)
