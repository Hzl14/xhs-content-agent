#!/usr/bin/env python3
"""Test the agent pipeline with sample notes."""
import json, httpx, asyncio

ITEMS = [
    {
        "title": "3款热门防晒真实测评，油皮别乱买",
        "content": "实测一周后给出结论：轻薄、防水、搓泥情况全对比。第一款安耐晒金瓶防晒力确实强但上脸有酒精感油皮夏天用容易闷痘。第二款理肤泉大哥大防晒力在线肤感更温和乳液质地不搓泥。第三款mistine小黄帽平价学生党首选防水防汗日常通勤够用暴晒不行。",
        "likes": 4521, "favorites": 2981, "comments": 376,
        "tags": ["防晒", "测评", "学生党"]
    },
    {
        "title": "早八通勤妆5分钟上脸，淡妆也有氛围感",
        "content": "底妆轻薄不假面重点是提亮和腮红位置。第一步防晒后直接上气垫不用妆前乳省时间。第二步散粉定妆只压T区保留脸颊光泽感。第三步液体腮红点涂苹果肌鼻尖下巴三处拍开氛围感就出来了。最后涂个有色润唇膏搞定全程真的只要5分钟。",
        "likes": 3210, "favorites": 1960, "comments": 221,
        "tags": ["通勤妆", "化妆教程", "平价彩妆"]
    },
    {
        "title": "平价面膜避雷清单：这些成分我再也不碰",
        "content": "敏感肌踩坑后整理了这份清单建议收藏。含有酒精的面膜敷完脸颊泛红刺痛真的伤皮肤屏障。香精味重的面膜虽然好闻但致敏率高敏感肌绕道走。还有那种敷完假滑感很重的其实加了大量增稠剂没有护肤效果。现在我只看械字号或者成分表前五位是玻尿酸神经酰胺积雪草的。",
        "likes": 2680, "favorites": 1702, "comments": 184,
        "tags": ["护肤避雷", "面膜", "敏感肌"]
    },
]


async def main():
    body = {
        "audience": "大学生女性",
        "tone": "真实分享",
        "topic_count": 2,
        "content_count_per_topic": 1,
        "min_final_note_count": 2,
        "items": ITEMS,
        "user_message": "我这有3篇参考笔记，直接基于它们生成选题和文案，不要再爬数据了。"
    }
    print("Calling /agent/run ...")
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post("http://127.0.0.1:8010/agent/run", json=body)
        result = resp.json()

    if resp.status_code == 200:
        print("\n=== Agent Run 成功 ===\n")
        print(f"Stage: {result.get('stage')}")
        print(f"Failed: {result.get('failed')}")
        if result.get('error_message'):
            print(f"Error: {result['error_message']}")
        print(f"Input notes: {result.get('input_note_count')}")
        print(f"Keywords: {result.get('top_keywords')}")
        print(f"Tags: {result.get('top_tags')}")
        print(f"Title patterns: {result.get('title_patterns')}")
        print(f"Insight: {result.get('insight_points')}")

        if result.get('draft_package'):
            print(f"\nDraft package: {result['draft_package'].get('draft_path', 'N/A')}")

        if result.get('results'):
            for i, topic_result in enumerate(result['results']):
                print(f"\n{'='*60}")
                print(f"选题 {i+1}: {topic_result.get('topic', {}).get('title', 'N/A')}")
                print(f"理由: {topic_result.get('topic', {}).get('reason', 'N/A')}")
                for j, content in enumerate(topic_result.get('contents', [])):
                    print(f"\n--- 文案 {j+1} ---")
                    print(f"标题: {content.get('title', 'N/A')}")
                    body_text = content.get('body', '')
                    print(f"正文 ({len(body_text)}字):")
                    print(body_text[:500])
                    print(f"标签: {content.get('tags', [])}")
                    print(f"CTA: {content.get('cta', 'N/A')}")
                    if content.get('critique'):
                        c = content['critique']
                        print(f"评审: 总分{c.get('total_score','?')} 过={c.get('passed',False)}")
    else:
        print(f"\nError {resp.status_code}:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

asyncio.run(main())
