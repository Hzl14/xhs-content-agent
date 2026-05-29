#!/usr/bin/env python3
"""Run agent and save results to file."""
import json, httpx, sys, io

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


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    body = {
        "session_id": "test_sunblock_" + __import__('uuid').uuid4().hex[:8],
        "audience": "大学生女性",
        "tone": "真实分享",
        "topic_count": 2,
        "content_count_per_topic": 1,
        "min_final_note_count": 2,
        "items": ITEMS,
        "user_message": "我想写一篇完整的小红书笔记，帮我做选题和内容生成。"
    }
    print("Calling /agent/run ...")
    resp = httpx.post("http://127.0.0.1:8010/agent/run", json=body, timeout=180)
    data = resp.json()

    # Save full JSON to file
    json_path = r"f:\Github project\xhs_content_agent-main\data\output\agent_result.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Full result saved to: {json_path}")

    # Print readable summary
    lines = []
    lines.append(f"Stage: {data.get('stage')}")
    lines.append(f"Failed: {data.get('failed')}")
    lines.append(f"Input notes: {data.get('input_note_count')}")
    lines.append(f"Top keywords: {data.get('top_keywords', [])}")
    lines.append(f"Top tags: {data.get('top_tags', [])}")

    results = data.get('results', [])
    if results:
        for i, r in enumerate(results):
            topic = r.get('topic', {})
            lines.append(f"\n{'='*60}")
            lines.append(f"选题{i+1}: {topic.get('title', '')}")
            lines.append(f"理由: {topic.get('reason', '')}")
            for j, c in enumerate(r.get('contents', [])):
                lines.append(f"\n--- 文案{j+1} ---")
                lines.append(f"标题: {c.get('title', '')}")
                lines.append(f"正文({len(c.get('body',''))}字):")
                lines.append(c.get('body', '')[:1000])
                lines.append(f"\n标签: {c.get('tags', [])}")
                lines.append(f"CTA: {c.get('cta', '')}")
                critique = c.get('critique')
                if critique:
                    lines.append(f"评审: 总分{critique.get('total_score','?')} 通过={critique.get('passed',False)}")

        # Save readable version too
        txt_path = r"f:\Github project\xhs_content_agent-main\data\output\agent_result.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write('\n'.join(lines))
        print(f"Readable result saved to: {txt_path}")
    else:
        lines.append(f"\nNo results. Error: {data.get('error_message', 'Unknown')}")
        lines.append(f"Stage: {data.get('stage')}")
        lines.append(f"Run ID: {data.get('run_id')}")

    print('\n'.join(lines))


if __name__ == "__main__":
    main()
