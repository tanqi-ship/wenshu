# app/legacy —— 从 askdb 搬入、待逐步替换

## 这里放什么

从 askdb 搬入的横切能力代码。**清单见项目根的 `MIGRATION.md`。**

## 三条规则

### 1. 不要把新逻辑写在这里

新代码一律写到 `app/engine/` 或 `app/llm/`。

原因：`app/legacy/` 用的是宽松 lint（`ruff-legacy.toml`，只查 `F`/`E9`），
CI 里非阻塞。往这里加新代码，等于新代码也不受 lint 约束。

### 2. 搬进来的东西必须经过一次"可测试性"改造

特别关键的一条：**任何会联网的代码，都必须改造成走 `app/llm` 的 Protocol。**

askdb 的 `llm_service.py` 直接 `httpx.post`。如果原样搬进来，
CI 就再也跑不了了——因为测试会在某处真的发请求。

改造方式：

```python
# 旧（不要这样）
async def call_local_llm(prompt, **kwargs):
    async with httpx.AsyncClient() as client:
        return await client.post(...)

# 新（搬进来时改成这样）
async def call_local_llm(prompt, *, client: LLMClient | None = None, **kwargs):
    client = client or build_llm(Settings.from_env())
    return await client.complete(...)
```

### 3. 每替换一个模块，就把它从这里移出去

替换完成 → 删除对应 legacy 文件 → 更新 `MIGRATION.md` 的清单。

目标是在某个时点**删掉整个 `app/legacy/` 和 `ruff-legacy.toml`**。
如果一个文件三年后还躺在这里，说明当初可能根本不该搬它。

## 待办：DEPENDENCIES.md

搬入完成后请补一份模块依赖映射，格式：

| legacy 模块 | 被谁替代 | 状态 |
|---|---|---|
| `connector_manager.py` | （长期保留，新引擎也用它） | 保留 |
| `sql_gen_service.py` | `app/engine/compiler/` | 待替换 |
| `query_orchestrator.py` | `app/engine/` 编排 | 待替换 |

回退时需要这张表。
