"""评测集与评测框架。

- :mod:`evaluators`    —— 各类指标：exec-match / faithfulness / citation-iou / steps
- :mod:`redteam_suite` —— 红队样本（越权 / 注入 / 数据泄露）
- :mod:`run_eval`      —— CI 入口：跑指标 + 阈值判定（可接 PR check）

数据集在 :mod:`evals.datasets`（JSONL 文件）。
"""
