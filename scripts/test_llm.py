#!/usr/bin/env python3
"""简单脚本：测试 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 是否可用。

用法：
  python scripts/test_llm.py

脚本会：
 - 尝试加载仓库根目录下的 `.env`（如果存在）
 - 组装常见的 OpenAI-compatible `/v1/chat/completions` 请求并发送
 - 打印响应（或错误信息）以便排查
"""
import os
import sys
import json
from urllib.parse import urljoin

try:
    import requests
except Exception:
    print("请先安装依赖：pip install requests", file=sys.stderr)
    raise

ROOT = os.path.dirname(os.path.dirname(__file__)) if os.path.isdir(os.path.dirname(__file__)) else os.getcwd()

def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            if '=' not in ln:
                continue
            k, v = ln.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # strip trailing comments
            if '#' in v:
                v = v.split('#', 1)[0].strip()
            # only set if not already in env
            if k not in os.environ:
                os.environ[k] = v

def sanitize_base_url(url: str) -> str:
    if not url:
        return url
    s = url.strip()
    # cut off at first whitespace or '#'
    for sep in ('#', ' '):
        if sep in s:
            s = s.split(sep, 1)[0]
    return s.rstrip('/')

def build_chat_completions_endpoint(base_url: str) -> str:
    """Support both base URLs with and without /v1 suffix."""
    normalized = base_url.rstrip('/')
    if normalized.endswith('/v1'):
        return urljoin(normalized + '/', 'chat/completions')
    return urljoin(normalized + '/', 'v1/chat/completions')

def env_truthy(name: str, default: str = '') -> bool:
    v = os.environ.get(name, default)
    if v is None:
        return False
    return v.strip().lower() in ('1', 'true', 'yes', 'on')

def collect_proxy_env() -> dict:
    keys = (
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
        'http_proxy', 'https_proxy', 'all_proxy',
        'NO_PROXY', 'no_proxy'
    )
    out = {}
    for k in keys:
        v = os.environ.get(k)
        if v:
            out[k] = v
    return out

def main():
    # try load .env from repo root
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    env_path = os.path.normpath(env_path)
    load_dotenv(env_path)

    api_key = os.environ.get('LLM_API_KEY')
    base_url = os.environ.get('LLM_BASE_URL')
    model = os.environ.get('LLM_MODEL')

    if not api_key:
        print('环境变量 LLM_API_KEY 未设置', file=sys.stderr)
        sys.exit(2)
    if not base_url:
        print('环境变量 LLM_BASE_URL 未设置', file=sys.stderr)
        sys.exit(2)
    if not model:
        print('环境变量 LLM_MODEL 未设置', file=sys.stderr)
        sys.exit(2)

    base_url = sanitize_base_url(base_url)
    # default to OpenAI-compatible chat completions
    endpoint = build_chat_completions_endpoint(base_url)
    disable_proxy = env_truthy('LLM_DISABLE_PROXY', '0')

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }

    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': "测试连接：请回复一个简短的 'pong'，并返回你使用的模型名。"}
        ],
        'max_tokens': 64,
        'temperature': 0.0,
    }

    print('请求地址:', endpoint)
    print('模型:', model)
    proxy_env = collect_proxy_env()
    if proxy_env:
        print('检测到代理环境变量:')
        for k, v in proxy_env.items():
            print(f' - {k}={v}')
    else:
        print('未检测到代理环境变量')
    if disable_proxy:
        print('代理模式: 已禁用（LLM_DISABLE_PROXY=true）')
    else:
        print('代理模式: 跟随环境变量（requests 默认行为）')

    try:
        session = requests.Session()
        if disable_proxy:
            session.trust_env = False
        r = session.post(endpoint, headers=headers, json=payload, timeout=30)
    except Exception as e:
        print('请求失败：', e, file=sys.stderr)
        sys.exit(3)

    print('HTTP', r.status_code)
    content_type = r.headers.get('Content-Type', '')
    if 'application/json' in content_type:
        try:
            j = r.json()
            print(json.dumps(j, ensure_ascii=False, indent=2))
        except Exception as e:
            print('解析 JSON 失败：', e, file=sys.stderr)
            print(r.text)
            sys.exit(4)
    else:
        print(r.text)

    if r.status_code >= 400:
        sys.exit(5)

if __name__ == '__main__':
    main()
