#!/usr/bin/env python3
"""Direct test of content generation endpoint."""
import json, httpx

body = {
    "audience": "大学生女性",
    "tone": "真实分享",
    "items": [
        {
            "title": "3款热门防晒真实测评，油皮别乱买",
            "content": "实测一周后给出结论：轻薄、防水、搓泥情况全对比。第一款安耐晒金瓶防晒力确实强但上脸有酒精感油皮夏天用容易闷痘。第二款理肤泉大哥大防晒力在线肤感更温和乳液质地不搓泥。第三款mistine小黄帽平价学生党首选防水防汗日常通勤够用暴晒不行。",
            "likes": 4521, "favorites": 2981, "comments": 376,
            "tags": ["防晒", "测评", "学生党"]
        }
    ],
    # Topic info is required by ContentGenerateRequest
    "topic": {
        "title": "学生党平价防晒推荐",
        "reason": "夏季防晒需求刚需，学生党预算有限需要平价推荐",
        "target_audience": "大学生女性",
        "style": "真实分享",
        "keywords": ["防晒", "平价", "学生党", "油皮", "测评"],
        "insights": ["防晒话题互动高", "测评类标题点击率高"]
    }
}

print("Testing /content/generate ...")
resp = httpx.post("http://127.0.0.1:8010/content/generate", json=body, timeout=120)
print(f"Status: {resp.status_code}")
data = resp.json()
print(json.dumps(data, ensure_ascii=False, indent=2))
