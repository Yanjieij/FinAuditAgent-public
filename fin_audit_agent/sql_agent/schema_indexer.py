"""离线 Schema 索引（启动前一次跑，后续直接读）。

**为什么要离线做**：
    每次 LLM 生成 SQL 都现查 ``information_schema`` 太慢，且描述信息（DBA 补的
    中文业务词义、样例行）不会高频变，适合 build 一次存起来。

**存储选择**：
    - 索引元数据 → SQLite（表：`tables`, `columns`, `glossary`）
    - 向量化嵌入 → Chroma（同样持久化到本地 `.chroma/`）

  这样 :mod:`schema_linker` 在检索时是**混合检索**：
    1. jieba 分词 + BM25 关键词命中表名/列注释
    2. 嵌入相似度命中语义（用户说"营收"，对上 "revenue" 列描述）
    3. RRF 融合 top-K

为什么不直接塞全 schema 给 LLM（prompt 里列全 100 张表）：
    - 上下文爆炸，GPT-4o 都会选错
    - 费钱：单次 SQL 生成就可能 5000+ tokens
    - Schema Linking 后通常能压到 800 tokens，10x 成本下降
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TableMeta:
    """一张表的元数据。"""

    name: str
    description: str
    business_domain: str = ""  # 如 "财务" / "HR" / "销售"
    row_count_hint: int = 0  # 粗略量级，仅供 planner 参考


@dataclass
class ColumnMeta:
    """一列的元数据。"""

    table: str
    name: str
    dtype: str
    description: str
    # PII/PCI 等级，交给 auth.column_tagger 读
    pii_level: int = 0  # 0=无 1=内部 2=敏感 3=极敏
    sample_values: list[str] | None = None


@dataclass
class GlossaryEntry:
    """业务术语 → 表/列的映射（DBA 手工维护）。

    例子::

        GlossaryEntry(term="应收账款周转率",
                      tables=["fact_ar_turnover"],
                      columns=["fact_ar_turnover.turnover_ratio"],
                      formula="revenue / avg(ar_balance)")
    """

    term: str
    tables: list[str]
    columns: list[str]
    formula: str = ""


class SchemaIndex:
    """SQLite 持久化 + 内存缓存的 schema 索引。

    目的是让 schema_linker 高频读的时候无需每次起 DB 连接。
    """

    def __init__(self, db_path: str | Path = ".fin_schema_index.db"):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """建表（幂等）。"""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tables_meta (
                name TEXT PRIMARY KEY,
                description TEXT,
                business_domain TEXT,
                row_count_hint INTEGER
            );
            CREATE TABLE IF NOT EXISTS columns_meta (
                tbl TEXT,
                name TEXT,
                dtype TEXT,
                description TEXT,
                pii_level INTEGER,
                sample_values TEXT,
                PRIMARY KEY (tbl, name)
            );
            CREATE TABLE IF NOT EXISTS glossary (
                term TEXT PRIMARY KEY,
                tables TEXT,
                columns TEXT,
                formula TEXT
            );
            """
        )
        self._conn.commit()

    # ---------- 写入 ----------
    def upsert_table(self, t: TableMeta) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO tables_meta VALUES (?,?,?,?)",
            (t.name, t.description, t.business_domain, t.row_count_hint),
        )

    def upsert_column(self, c: ColumnMeta) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO columns_meta VALUES (?,?,?,?,?,?)",
            (
                c.table,
                c.name,
                c.dtype,
                c.description,
                c.pii_level,
                json.dumps(c.sample_values or [], ensure_ascii=False),
            ),
        )

    def upsert_glossary(self, g: GlossaryEntry) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO glossary VALUES (?,?,?,?)",
            (
                g.term,
                json.dumps(g.tables, ensure_ascii=False),
                json.dumps(g.columns, ensure_ascii=False),
                g.formula,
            ),
        )

    def commit(self) -> None:
        self._conn.commit()

    # ---------- 读取 ----------
    def all_tables(self) -> list[TableMeta]:
        rows = self._conn.execute("SELECT * FROM tables_meta").fetchall()
        return [TableMeta(**dict(r)) for r in rows]

    def columns_of(self, table: str) -> list[ColumnMeta]:
        rows = self._conn.execute(
            "SELECT * FROM columns_meta WHERE tbl=?", (table,)
        ).fetchall()
        return [
            ColumnMeta(
                table=r["tbl"],
                name=r["name"],
                dtype=r["dtype"],
                description=r["description"],
                pii_level=r["pii_level"],
                sample_values=json.loads(r["sample_values"] or "[]"),
            )
            for r in rows
        ]

    def all_glossary(self) -> list[GlossaryEntry]:
        rows = self._conn.execute("SELECT * FROM glossary").fetchall()
        return [
            GlossaryEntry(
                term=r["term"],
                tables=json.loads(r["tables"]),
                columns=json.loads(r["columns"]),
                formula=r["formula"],
            )
            for r in rows
        ]


def build_demo_index(db_path: str | Path = ".fin_schema_index.db") -> SchemaIndex:
    """造一个玩具 schema 索引，examples / tests 用得上。

    对应虚构的财务数据模型：
        - ``dim_department``    部门维度
        - ``dim_employee``      员工维度（含薪资 PII）
        - ``fact_expense``      费用事实表
        - ``fact_revenue``      营收事实表
    """
    idx = SchemaIndex(db_path)

    idx.upsert_table(TableMeta("dim_department", "部门维度表", "组织"))
    idx.upsert_table(TableMeta("dim_employee", "员工维度表（含 PII）", "HR"))
    idx.upsert_table(TableMeta("fact_expense", "费用事实表（差旅、办公、营销）", "财务"))
    idx.upsert_table(TableMeta("fact_revenue", "营收事实表（按部门按月）", "财务"))

    # dim_employee 的 salary 是极敏感 PII
    idx.upsert_column(ColumnMeta("dim_employee", "emp_id", "INT", "员工 ID", 0))
    idx.upsert_column(ColumnMeta("dim_employee", "name", "TEXT", "姓名", 2))
    idx.upsert_column(ColumnMeta("dim_employee", "id_card", "TEXT", "身份证号", 3))
    idx.upsert_column(ColumnMeta("dim_employee", "salary", "DECIMAL", "月薪（税前）", 3))
    idx.upsert_column(ColumnMeta("dim_employee", "dept_id", "INT", "所属部门 ID", 0))

    idx.upsert_column(ColumnMeta("fact_expense", "id", "INT", "费用单号", 0))
    idx.upsert_column(ColumnMeta("fact_expense", "dept_id", "INT", "部门 ID", 0))
    idx.upsert_column(ColumnMeta("fact_expense", "amount", "DECIMAL", "报销金额（元）", 0))
    idx.upsert_column(ColumnMeta("fact_expense", "category", "TEXT", "费用类别 差旅/办公/营销", 0))
    idx.upsert_column(ColumnMeta("fact_expense", "occurred_at", "DATE", "发生日期", 0))

    idx.upsert_column(ColumnMeta("fact_revenue", "dept_id", "INT", "部门 ID", 0))
    idx.upsert_column(ColumnMeta("fact_revenue", "period", "TEXT", "账期 YYYYMM", 0))
    idx.upsert_column(ColumnMeta("fact_revenue", "amount", "DECIMAL", "营收（元）", 0))

    idx.upsert_glossary(
        GlossaryEntry(
            term="销售费用率",
            tables=["fact_expense", "fact_revenue"],
            columns=["fact_expense.amount", "fact_revenue.amount"],
            formula="sum(expense.amount where category='营销') / sum(revenue.amount)",
        )
    )
    idx.upsert_glossary(
        GlossaryEntry(
            term="部门超支",
            tables=["fact_expense", "dim_department"],
            columns=["fact_expense.amount", "dim_department.budget"],
            formula="sum(expense.amount) - sum(department.budget) per period",
        )
    )

    idx.commit()
    return idx
