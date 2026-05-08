import sys
import wikipediaapi
import os

PROXY_URL = "http://127.0.0.1:7890"
# Apply proxy globally so both `wikipedia` and `wikipediaapi` use it.
os.environ["HTTP_PROXY"] = PROXY_URL
os.environ["HTTPS_PROXY"] = PROXY_URL
os.environ["http_proxy"] = PROXY_URL
os.environ["https_proxy"] = PROXY_URL

query = " ".join(sys.argv[1:])
lang = "en"  # 可改为 'zh'

# 获取页面对象

wiki_wiki = wikipediaapi.Wikipedia(user_agent="MyBot/1.0",  # 必填
                                    language="en",
                                    extract_format=wikipediaapi.ExtractFormat.WIKI,
                                    proxy=PROXY_URL,
                                    timeout=20.0,
                                    max_retries=3)
page = wiki_wiki.page(query)
print(f"标题: {page.title}")
if not page.exists():
    print("未找到条目")
else:
    # 输出摘要（wikipedia-api 需要自己截取句子）
    summary = page.summary
    print(f"摘要示例：{summary}")
    sentences = summary.split(". ")[:3]  # 取前3句作为示例
    print(". ".join(sentences) + ("" if summary.endswith(".") else "."))

    # 提示内部链接（可选，展示歧义或关联条目）
    links = list(page.links.keys())
    if len(links) > 10:
        print("\n相关条目示例：", links[:10])