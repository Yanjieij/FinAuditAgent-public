"""Example 02 —— Text-to-SQL + Schema Linking + 语义层 + 容错重试。

演示 4 件事：
    1. 构建一个玩具 SchemaIndex（:mod:`sql_agent.schema_indexer.build_demo_index`）
    2. 用 Schema Linking 把用户问题压缩成"相关表子集 prompt"
    3. 用 Semantic Layer 统一指标口径（``{{ measure:销售费用率 }}`` 占位符）
    4. 本 example **不调真实 LLM**（避免烧 key），而是用 FakeLLM 打桩展示 retry_loop 闭环

跑::

    conda activate fin-audit-agent
    python examples/02_sql_schema_linking.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rich.console import Console

from fin_audit_agent.sql_agent.executor import SqlExecutor
from fin_audit_agent.sql_agent.retry_loop import run as sql_run
from fin_audit_agent.sql_agent.schema_indexer import build_demo_index
from fin_audit_agent.sql_agent.schema_linker import link_schema
from fin_audit_agent.sql_agent.semantic_layer import SemanticLayer
from fin_audit_agent.sql_agent.sql_gen import SQLGenResult

console = Console()

DEMO_DB = Path(".demo_fin.db")
SCHEMA_DB = Path(".demo_fin_schema.db")


# --- 1. 造一点数据（SQLite） ---
def seed_demo_db() -> None:
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    conn = sqlite3.connect(DEMO_DB)
    conn.executescript(
        """
        CREATE TABLE dim_department (dept_id INT PRIMARY KEY, name TEXT, budget DECIMAL);
        CREATE TABLE dim_employee   (emp_id INT, name TEXT, id_card TEXT, salary DECIMAL, dept_id INT);
        CREATE TABLE fact_expense   (id INT, dept_id INT, amount DECIMAL, category TEXT, occurred_at DATE);
        CREATE TABLE fact_revenue   (dept_id INT, period TEXT, amount DECIMAL);

        INSERT INTO dim_department VALUES
          (1,'市场部',500000), (2,'研发部',1200000), (3,'销售部',800000);
        INSERT INTO dim_employee VALUES
          (101,'张三','110101199001010011',20000,1),
          (102,'李四','110101199201020022',35000,2);
        INSERT INTO fact_expense VALUES
          (1,1,180000,'营销','2025-01-10'),(2,1,220000,'营销','2025-02-15'),
          (3,2,30000,'办公','2025-01-20'),(4,3,90000,'差旅','2025-03-05'),
          (5,1,650000,'营销','2025-03-25');  -- 超预算！
        INSERT INTO fact_revenue VALUES
          (1,'202501',800000),(1,'202502',900000),(1,'202503',1200000),
          (2,'202501',500000),(3,'202501',700000);
        """
    )
    conn.commit()
    conn.close()


# --- 2. FakeLLM：不打真 key，模拟 2 次错 1 次对 ---
class FakeLLM:
    """模拟一个会自愈的 LLM。"""

    def __init__(self):
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        # 第 1 次故意返回错列名
        if self.call_count == 1:
            return type("M", (), {"content": '{"sql": "SELECT non_exist_col FROM fact_expense LIMIT 10", "rationale": "初次猜测", "need_clarify": null}'})()
        # 第 2 次返回正确（用 measure 占位符）
        return type("M", (), {"content": (
            '{"sql": "SELECT d.name AS 部门, SUM(fact_expense.amount) AS 营销费用合计 '
            'FROM fact_expense JOIN dim_department d ON fact_expense.dept_id=d.dept_id '
            "WHERE fact_expense.category='营销' GROUP BY d.name\", "
            '"rationale": "按部门汇总营销费用", "need_clarify": null}'
        )})()


def main():
    seed_demo_db()
    console.rule("[bold]构建 Schema 索引[/bold]")
    idx = build_demo_index(SCHEMA_DB)
    console.print(f"索引了 {len(idx.all_tables())} 张表")

    question = "请按部门统计 2025 年 1-3 月的营销费用合计"

    console.rule("[bold]Schema Linking[/bold]")
    linked = link_schema(question, idx)
    console.print(linked.to_prompt_block())

    console.rule("[bold]Semantic Layer[/bold]")
    sem = SemanticLayer.demo()
    console.print(sem.to_prompt_block())

    console.rule("[bold]跑 retry_loop（用 FakeLLM 模拟第 1 次错列名 → 第 2 次自愈）[/bold]")
    executor = SqlExecutor(db_url=f"sqlite:///{DEMO_DB}")
    llm = FakeLLM()
    outcome = sql_run(question, idx, sem, executor=executor, llm=llm, max_retries=3)

    console.print(f"[green]ok[/green]={outcome.ok}, attempts={outcome.attempts}")
    if outcome.ok:
        console.print(f"final_sql = [cyan]{outcome.final_sql}[/cyan]")
        console.print(outcome.df)
    else:
        console.print(f"[red]failed[/red]: {outcome.last_error}")
        console.print(f"clarify: {outcome.clarify_question}")

    console.print("\n轨迹：")
    for step in outcome.trace:
        console.print(f"  attempt {step['attempt']}: {step.get('verdict')}  sql={step.get('sql_validated') or step.get('sql_raw','')[:80]}")


if __name__ == "__main__":
    main()
