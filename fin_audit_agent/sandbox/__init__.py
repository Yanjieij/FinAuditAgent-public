"""痛点 1：零幻觉 + 精准计算。

核心思想：**LLM 不直接吐数字，它只写代码；数字从沙箱执行结果回填到模板。**

模块分工：
    - :mod:`runner`          —— 本地沙箱（``RestrictedPython``），macOS 友好，默认路径
    - :mod:`e2b_runner`      —— 生产云沙箱（``e2b``）骨架；真实部署换上这个
    - :mod:`artifact_store`  —— 产物（DataFrame/图表）落盘 + 生成 exec_id
    - :mod:`result_schema`   —— 统一的执行结果结构
    - :mod:`number_verifier` —— **Evidence-pointer 输出契约**：数字必须带 exec_id 引用

沙箱方案完整对照（见 docs/01_sandbox_guide.md）：

    ==================  ================  ================  =================
    方案                冷启              安全边界          适用
    ==================  ================  ================  =================
    Pyodide (WASM)      100ms             完全隔离           浏览器，无 pandas
    e2b 云沙箱          300-500ms         强（独立 VM）      生产推荐
    Docker + gVisor     500-1000ms        强                 自建集群
    nsjail + seccomp    50ms              中-强（配置严格）   **生产最优**
    RestrictedPython    ~0ms              弱（进程内）        **本地开发**
    ==================  ================  ================  =================
"""
