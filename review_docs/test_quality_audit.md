# A2 测试质量审计报告

## 执行摘要

A2 的测试套件覆盖了**基础的数据持久化和查询逻辑**，但**缺少关键的语义测试**。特别是：
- **没有测试 tool call 保留逻辑**
- **没有测试两次压缩的行为**
- **没有测试压缩失败路径**
- **没有验证压缩后会话的完整性**

总体测试可信度：**中等**（60/100）

---

## 第一部分：高质量测试

### ✓ SessionStore 基础测试（test_session_store.py）

1. **TestCreateAndMessages::test_append_message_ordering**
   ```python
   store.append_message(sid, {"role": "user", "content": "hi"})
   store.append_message(sid, {"role": "assistant", "content": "yo"})
   rows = store.list_messages(sid)
   assert rows[0].metadata.seq == 0 and rows[0].metadata.role == "user"
   assert rows[1].metadata.seq == 1 and rows[1].metadata.role == "assistant"
   ```
   **质量**：✓ 良好 - 验证了 seq 顺序的准确性

2. **TestCreateAndMessages::test_append_tool_calls_rows**
   ```python
   store.append_message(
       sid,
       {
           "role": "assistant",
           "tool_calls": [
               {"id": "call_1", "type": "function", "function": {"name": "demo_tool", ...}}
           ],
       },
   )
   cur = store._conn.execute("SELECT COUNT(*) FROM tool_calls WHERE message_id = ?", ...)
   assert cur.fetchone()[0] == 1
   ```
   **质量**：✓ 良好 - 深入数据库验证，确保 tool_calls 表正确

3. **TestConcurrentAppend::test_concurrent_appends_same_session**
   ```python
   threads = [threading.Thread(target=worker) for i in range(4)]
   # 4 个线程，每个 5 次 append
   assert len(rows) == 20  # 验证没有丢失
   ```
   **质量**：✓ 很好 - 验证了并发安全性

4. **TestReplaceMessages::test_replace_session_messages_resets_seq**
   ```python
   store.replace_session_messages(sid, [{"role": "user", "content": "only"}], ...)
   rows = store.list_messages(sid)
   assert rows[0].metadata.seq == 0
   ```
   **质量**：✓ 好 - 验证了消息替换时 seq 被正确重置

### ✓ 会话状态测试

1. **TestCreateAndMessages::test_archive_blocks_append**
   ```python
   store.archive_session(sid)
   with pytest.raises(SessionArchivedError):
       store.append_message(sid, {"role": "user", "content": "x"})
   ```
   **质量**：✓ 好 - 验证了 ARCHIVED 状态的约束

---

## 第二部分：伪测试（虽然通过但验证不足）

### ⚠️ test_compress_static_fallback_when_summarizer_none

**代码**：
```python
out = compress_messages(
    messages,  # 16 条（8 轮）
    token_limit=500,
    model_name="gpt-4o-mini",
    summarizer=None,
    use_llm_summarizer=False,
    protect_last_rounds=3,
)
assert any(m.get("role") == "system" for m in out)
assert "[CONTEXT COMPACTION]" in (out[0].get("content") or "")
```

**问题**：
1. 只验证"有 system message"，没有验证**内容**
2. 没有验证**消息总数是否正确**
3. 没有验证**最后 3 轮是否完全保留**
4. 没有验证**中间消息是否被删除**
5. 没有验证**token 数是否真的降低**

**应该补充的验证**：
```python
# 验证 tail 完全保留
tail_users = [i for i, m in enumerate(messages) if m.get("role") == "user"][-3:]
tail_start = tail_users[0]
expected_tail_count = len(messages) - tail_start
actual_tail_count = len([m for m in out if m.get("role") != "system"])
assert actual_tail_count == expected_tail_count, f"尾部消息不完全：{actual_tail_count} vs {expected_tail_count}"

# 验证中间消息被删除
assert len(out) == (expected_tail_count + 1)  # 1 个 summary + tail

# 验证 summary 内容
summary = [m for m in out if m.get("role") == "system"][0]
assert summary["content"].count("Summary") > 0 or summary["content"].count("removed") > 0
```

**评级**：⚠️ **伪测试** - 断言太弱

---

### ⚠️ test_context_manager_compression_persists

**代码**：
```python
mgr.check_and_compress(sid)
rows = store.list_messages(sid, limit=200)
roles = [r.metadata.role for r in rows]
assert "system" in roles
```

**问题**：
1. 只检查"是否有 system"
2. 没有验证压缩**是否真的触发了**
3. 没有验证压缩后的**消息总数**
4. 没有验证**消息是否可以继续被使用**

**应该补充的验证**：
```python
# 记录压缩前的数据
before_compress = store.list_messages(sid, limit=200)
before_count = len(before_compress)
before_tokens = sum(m.metadata.token_count for m in before_compress)

# 压缩
mgr.check_and_compress(sid)
after_compress = store.list_messages(sid, limit=200)
after_count = len(after_compress)
after_tokens = sum(m.metadata.token_count for m in after_compress)

# 验证
assert after_tokens < before_tokens, f"Token 没有降低：{after_tokens} vs {before_tokens}"
assert after_count <= before_count + 1, f"消息数增加了：{after_count} vs {before_count}"

# 验证可以继续对话
messages = mgr.load_context(sid)
assert len(messages) > 0
assert messages[-1]["role"] in ("assistant", "user")
```

**评级**：🔴 **伪测试** - 几乎没有验证

---

## 第三部分：过度 Mock 的测试

### test_agent_loop.py 中的 Agent 测试

**问题**：
```python
agent._client.chat.completions.create.return_value = _make_response(
    [_make_choice(content="回复")]
)
```

所有 LLM 调用都被 mock，这导致：
1. **压缩逻辑无法被真实测试**
   - 压缩需要真实的 token 估计
   - Mock 返回固定的 prompt_tokens，无法验证压缩是否真的触发

2. **工具调用链无法被完整验证**
   - 工具调用的整个流程被 mock，无法验证持久化

3. **错误处理无法被验证**
   - API 失败、限流等情况无法测试

**建议**：
- 至少有一些集成测试不 mock LLM（或使用 OpenAI 的测试环境）
- 或者至少 mock 返回真实的 token 数

---

## 第四部分：缺失的重要测试

### 1. ❌ Tool Call 保留测试

**应该测试**：
```python
def test_tool_call_in_tail_preserved():
    """Tool call in tail should be preserved."""
    messages = [...]  # tool call at index len-2
    out = compress_messages(messages, protect_last_rounds=1, ...)
    assert any(m.get("tool_calls") for m in out)

def test_tool_call_in_middle_dropped():
    """Tool call in middle should be dropped."""
    messages = [...]  # tool call at index 1
    out = compress_messages(messages, protect_last_rounds=3, ...)
    assert not any(m.get("tool_calls") for m in out)  # ← 这个应该失败，因为现在不满足约束
```

**当前**：完全缺失 ❌

### 2. ❌ 再次压缩测试

**应该测试**：
```python
def test_compress_twice():
    """压缩一次，再压缩一次，应该不会丢失 summary"""
    messages = [...]
    
    # 第一次压缩
    out1 = compress_messages(messages, ...)
    assert any(m.get("role") == "system" for m in out1)
    
    # 第二次压缩（在 out1 的基础上）
    out2 = compress_messages(out1, ...)
    assert any(m.get("role") == "system" for m in out2)
    # Summary 应该还在
```

**当前**：完全缺失 ❌

### 3. ❌ 压缩失败路径测试

**应该测试**：
```python
def test_compression_fail_with_token_overflow():
    """即使压缩，仍然溢出的情况"""
    messages = [...]  # 超级长
    
    with pytest.raises(ContextOverflowError):
        compress_messages(messages, ...)
```

**当前**：完全缺失 ❌

### 4. ❌ 会话恢复完整性测试

**应该测试**：
```python
def test_session_resume_complete():
    """恢复会话后，内容、顺序、工具调用都应该正确"""
    # 创建会话
    store.create_session("user1")
    store.append_message(sid, {"role": "user", "content": "Q1"})
    store.append_message(sid, {
        "role": "assistant",
        "tool_calls": [{"id": "t1", ...}],
        ...
    })
    
    # 恢复
    store2 = SessionStore(same_db_path)
    rows = store2.list_messages(sid)
    
    # 详细验证
    assert rows[0].metadata.role == "user"
    assert rows[0].content == "Q1"
    assert rows[1].tool_calls is not None
    assert rows[1].tool_calls[0]["id"] == "t1"
    assert rows[1].metadata.seq == 1  # seq 正确
```

**当前**：TestSessionResume 只检查了 `len(a2.messages) >= 2` ❌

### 5. ❌ 压缩后恢复测试

**应该测试**：
```python
def test_session_recover_after_compression():
    """压缩后恢复会话，应该看到 summary message"""
    # 创建超长会话
    for i in range(20):
        store.append_message(sid, {"role": "user", "content": f"Q{i}"})
        store.append_message(sid, {"role": "assistant", "content": f"A{i}"})
    
    # 压缩
    messages = [m.to_openai_dict() for m in store.list_messages(sid, limit=1000)]
    compressed = compress_messages(messages, ...)
    store.replace_session_messages(sid, compressed, ...)
    
    # 恢复
    rows2 = store.list_messages(sid)
    assert any(r.metadata.role == "system" for r in rows2)
    
    # 验证可继续对话
    store.append_message(sid, {"role": "user", "content": "follow-up"})
```

**当前**：完全缺失 ❌

---

## 第五部分：不专业的断言

### 例 1：test_session_resume_loads_prior_messages

```python
assert len(a2.messages) >= 2  # ← 太弱
```

应该：
```python
assert len(a2.messages) >= 2
assert a2.messages[0]["role"] == "user"
assert a2.messages[1]["role"] == "assistant"
assert a2.messages[0]["content"] == "hello resume"  # 检查内容
```

### 例 2：test_rough_vs_tiktoken_order

```python
assert rough > 0 and precise > 0  # ← 没有验证大小关系
```

应该：
```python
# 粗估通常高于精确值，但应该同一数量级
assert precise > 0
assert rough > 0
assert abs(rough - precise) < precise * 0.5 or rough > precise
```

---

## 第六部分：Fixture 和隔离问题

### ✓ 好的隔离

1. **test_session_store.py** 使用 `tmp_path` fixture，每个测试都有独立的 DB
2. **test_agent_loop.py** 中的 agent fixture 使用 `tmp_path`
3. **store.close()** 被正确调用（在 yield 之后）

### ⚠️ 可改进的地方

1. **test_agent_loop.py 中 agent fixture 可能被重用**
   - 当前是 `@pytest.fixture()` 不带 scope，默认 `function` scope（好）
   - 但没有显式说明

2. **临时 DB 文件清理**
   - SQLite DB 文件在 pytest 完成后由 tmp_path 自动清理（好）
   - 但没有显式验证 WAL 文件被清理

---

## 第七部分：综合评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码覆盖率 | 70% | 主要的代码路径被测试 |
| 语义覆盖率 | 40% | 关键的业务逻辑没有被验证 |
| 边界测试 | 30% | 缺少错误情况的测试 |
| 集成测试 | 20% | 缺少端到端的测试 |
| 断言质量 | 50% | 很多断言太弱 |
| Fixture 质量 | 80% | 隔离做得很好 |

**综合得分**：**55/100** - 可以进行基础回归测试，但不足以验证设计约束

---

## 最终建议

### 必须添加的测试

1. **Tool call 保留测试**（2 小时）
   - 测试 tool call 在尾部时被保留
   - 测试 tool call 在中间时被保留（新增约束）

2. **再次压缩测试**（1 小时）
   - 验证 summary message 不被删除

3. **压缩失败测试**（1 小时）
   - 验证 ContextOverflowError 被抛出

4. **会话恢复完整性测试**（1.5 小时）
   - 验证消息内容、顺序、tool_calls

### 可选改进

5. 压缩后恢复测试
6. 集成测试（使用真实的 LLM 或更真实的 mock）
7. 性能测试（大量消息的压缩速度）

**总工作量**：5.5 小时

---

## 结论

A2 的测试提供了**基础的数据完整性验证**，但**缺少语义层面的验证**。如果不补充上述测试，无法确保实现符合 Phase2 设计约束。

**建议**：在修复代码问题的同时，立即补充上述 4 项必须的测试。

