# tests/legacy —— 搬来的 askdb 测试

## 现状

askdb 有 49 个测试文件。搬入后放这里。

## CI 策略

**非阻塞。** `ci.yml` 里对应的 job 带 `continue-on-error: true`。

原因：这些测试依赖 askdb 的完整环境（Oracle 连接、千帆 KB、特定 fixture），
在新仓库里大部分会失败。一上来就要求它们全绿，只会让人关掉整个 CI。

## 但要知道自己在测什么

`pytest.ini` 的 `testpaths = ["tests"]` 会把这些也跑起来。
如果它们的失败噪音太大，两个选择：

1. 用 marker 排除：`addopts = "-m 'not legacy'"`
2. 直接删除确实无意义的测试

**不要做的事**：用 `--ignore` 悄悄跳过却不记录原因。
半年后没人知道为什么这些测试不跑 —— 这正是 auto-ontology 的 CI 注释里
点名要防止的情况（"a suite turned off silently and still reports green"）。

## 搬入时的改造清单

- [ ] 把 `httpx.post` 的 mock 换成 `FakeLLM`
- [ ] 把真实数据库依赖换成 fixture 库（或打 `@pytest.mark.db`）
- [ ] 把跳过标记里的依赖名改正（依赖名写错的 skip 会让测试静默永不执行）
