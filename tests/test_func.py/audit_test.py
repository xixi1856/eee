#!/usr/bin/env python
"""Audit script for A2 implementation."""
from edu_agent.context.compressor import compress_messages

# Test 1: Verify compression preserves tail
print("=== Test 1: Compression preserves tail ===")
many_users = [{'role': 'user', 'content': f'turn-{i}'} for i in range(8)]
many_asst = [{'role': 'assistant', 'content': f'reply-{i}'} for i in range(8)]
messages = []
for i in range(8):
    messages.append(many_users[i])
    messages.append(many_asst[i])

print(f'Original messages: {len(messages)}')
print(f'Roles: {[m["role"] for m in messages]}')

out = compress_messages(
    messages,
    token_limit=500,
    model_name='gpt-4o-mini',
    summarizer=None,
    use_llm_summarizer=False,
    protect_last_rounds=3,
)

print(f'\nCompressed messages: {len(out)}')
print(f'Roles: {[m["role"] for m in out]}')
print(f'Has summary: {any(m.get("role") == "system" for m in out)}')

# Test 2: Verify _is_summary is preserved in dict
print("\n=== Test 2: _is_summary in compressed messages ===")
has_is_summary = any(m.get('_is_summary') for m in out)
print(f'Has _is_summary flag: {has_is_summary}')
summary_msg = [m for m in out if m.get('role') == 'system']
if summary_msg:
    print(f'Summary msg keys: {summary_msg[0].keys()}')

# Test 3: Check if summary is droppable
print("\n=== Test 3: Is summary droppable? ===")
from edu_agent.context.compressor import _droppable  # noqa: E402
summary = out[0] if out and out[0].get('role') == 'system' else None
if summary:
    print(f'Summary message is droppable: {_droppable(summary)}')

# Test 4: Verify tool_call preservation
print("\n=== Test 4: Tool call preservation ===")
messages_with_tool = [
    {'role': 'user', 'content': 'help'},
    {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call1', 'type': 'function', 'function': {'name': 'test_tool', 'arguments': '{}'}}
    ]},
]

messages_with_tool.extend(
    {'role': 'user', 'content': f'turn-{i}'}
    for i in range(10)
)
messages_with_tool = [
    {'role': 'user', 'content': 'help'},
    {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call1', 'type': 'function', 'function': {'name': 'test_tool', 'arguments': '{}'}}
    ]},
] + [{'role': 'user', 'content': f'turn-{i}'} for i in range(10)]
messages_with_tool += [{'role': 'assistant', 'content': f'reply-{i}'} for i in range(10)]

out2 = compress_messages(
    messages_with_tool,
    token_limit=100,
    model_name='gpt-4o-mini',
    summarizer=None,
    use_llm_summarizer=False,
    protect_last_rounds=1,
)

print(f'Original with tool: {len(messages_with_tool)}')
print(f'Compressed: {len(out2)}')
has_tool = any(m.get('tool_calls') for m in out2)
print(f'Has tool_calls after compression: {has_tool}')

print("\n=== Audit tests completed ===")
