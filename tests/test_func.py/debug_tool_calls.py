#!/usr/bin/env python
"""Debug tool call preservation."""
from edu_agent.context.compressor import compress_messages, _tail_start_index

# Create messages with tool call at the beginning
messages = [
    {'role': 'user', 'content': 'help'},
    {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call1', 'type': 'function', 'function': {'name': 'test_tool', 'arguments': '{}'}}
    ]},
]
# Add more messages after
for i in range(5):
    messages.append({'role': 'user', 'content': f'turn-{i}'})
    messages.append({'role': 'assistant', 'content': f'reply-{i}'})

print(f"Original messages: {len(messages)}")
for i, m in enumerate(messages):
    tc_info = " (has tool_calls)" if m.get('tool_calls') else ""
    print(f"  [{i}] {m['role']}{tc_info}")

# Check what _tail_start_index would return
tail_start = _tail_start_index(messages, 3)
print(f"\n_tail_start_index returns: {tail_start}")
print(f"This means messages[0:{tail_start}] are middle (to be compressed)")
print(f"And messages[{tail_start}:] are tail (to be preserved)")

# Now compress
out = compress_messages(
    messages,
    token_limit=200,
    model_name='gpt-4o-mini',
    summarizer=None,
    use_llm_summarizer=False,
    protect_last_rounds=3,
)

print(f"\nCompressed messages: {len(out)}")
for i, m in enumerate(out):
    tc_info = " (has tool_calls)" if m.get('tool_calls') else ""
    content_preview = m['content'][:50] if isinstance(m['content'], str) else str(m['content'])[:50]
    print(f"  [{i}] {m['role']} {content_preview}{tc_info}")

# Check if tool_call message is in tail
print(f"\nOriginal tool call was at index 1, tail_start was {tail_start}")
print(f"So tool call message is in {'TAIL (preserved)' if 1 >= tail_start else 'MIDDLE (compressed)'}")

# Let's test with tool call in tail
print("\n" + "="*50)
print("Test 2: Tool call in tail region")
print("="*50)

messages2 = []
# Add many messages to push the tool call into tail region
for i in range(8):
    messages2.append({'role': 'user', 'content': f'old-{i}'})
    messages2.append({'role': 'assistant', 'content': f'reply-{i}'})

# Add tool call near end
messages2.append({'role': 'user', 'content': 'help'})
messages2.append({'role': 'assistant', 'content': None, 'tool_calls': [
    {'id': 'call1', 'type': 'function', 'function': {'name': 'test_tool', 'arguments': '{}'}}
]})

print(f"Messages with late tool call: {len(messages2)}")
tail_start2 = _tail_start_index(messages2, 2)
print(f"_tail_start_index returns: {tail_start2}")
print(f"Tool call is at index {len(messages2)-1}, so it's in {'TAIL' if len(messages2)-1 >= tail_start2 else 'MIDDLE'}")

out2 = compress_messages(
    messages2,
    token_limit=200,
    model_name='gpt-4o-mini',
    summarizer=None,
    use_llm_summarizer=False,
    protect_last_rounds=2,
)

has_tool_in_compressed = any(m.get('tool_calls') for m in out2)
print(f"\nCompressed has tool_calls: {has_tool_in_compressed}")
if has_tool_in_compressed:
    print("✓ Tool calls preserved when in tail region")
else:
    print("✗ Tool calls NOT preserved")

print("\n=== Analysis complete ===")
