"""
临时测试脚本：使用本地 T2I 端点渲染排行 HTML，保存到插件目录。
运行：python render_test.py
"""
import asyncio
import json
import aiohttp
import ssl
import certifi

T2I_BASE = "http://192.168.0.47:8999/text2img"

# 来自 main.py 的模板
TMPL = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { width: fit-content; background: transparent; }
  </style>
</head>
<body>
<div style="width: 700px; font-family: 'Microsoft YaHei', sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); border-radius: 20px; padding: 40px; color: #e0e0e0;">
  <div style="text-align: center; margin-bottom: 28px;">
    <h2 style="margin: 0; font-size: 30px; color: #e94560;">{{ group_name }} 好感度排行</h2>
    <p style="margin: 8px 0 0; font-size: 15px; color: #888;">TOP {{ rank_data|length }}</p>
  </div>
  {% for item in rank_data %}
  <div style="display: flex; align-items: center; padding: 14px 20px; margin-bottom: 10px; background: rgba(255,255,255,0.06); border-radius: 14px; {% if item.rank <= 3 %}border: 1px solid rgba(233,69,96,0.3);{% endif %}">
    <div style="width: 40px; text-align: center; font-size: 25px; font-weight: bold; {% if item.rank == 1 %}color: #ffd700;{% elif item.rank == 2 %}color: #c0c0c0;{% elif item.rank == 3 %}color: #cd7f32;{% else %}color: #888;{% endif %}">
      {{ item.rank }}
    </div>
    {% if item.avatar %}
    <img src="{{ item.avatar }}" style="width: 48px; height: 48px; border-radius: 50%; margin: 0 16px; object-fit: cover;" />
    {% else %}
    <div style="width: 48px; height: 48px; border-radius: 50%; margin: 0 16px; background: #333; display: flex; align-items: center; justify-content: center; font-size: 20px; color: #666;">{{ item.nickname[0] }}</div>
    {% endif %}
    <div style="flex: 1; min-width: 0;">
      <div style="font-size: 16px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{{ item.nickname }}</div>
      <div style="font-size: 13px; color: #888; margin-top: 3px;">ID: {{ item.user_id }}</div>
    </div>
    <div style="text-align: right; margin-left: 16px;">
      <div style="font-size: 20px; font-weight: bold; {% if item.affection >= 0 %}color: #4ecca3;{% else %}color: #e94560;{% endif %}">{{ item.affection }}</div>
      <div style="font-size: 13px; color: #aaa; margin-top: 3px;">{{ item.level }}</div>
    </div>
  </div>
  {% endfor %}
</div>
</body>
</html>"""

# 测试数据
TMPL_DATA = {
    "group_name": "测试群聊",
    "rank_data": [
        {"rank": 1, "user_id": "10001", "nickname": "小明", "affection": 95, "level": "死党", "avatar": "https://q1.qlogo.cn/g?b=qq&nk=10001&s=640"},
        {"rank": 2, "user_id": "10002", "nickname": "小红", "affection": 82, "level": "死党", "avatar": "https://q1.qlogo.cn/g?b=qq&nk=10002&s=640"},
        {"rank": 3, "user_id": "10003", "nickname": "小刚", "affection": 67, "level": "朋友", "avatar": "https://q1.qlogo.cn/g?b=qq&nk=10003&s=640"},
        {"rank": 4, "user_id": "10004", "nickname": "小丽", "affection": 45, "level": "朋友", "avatar": ""},
        {"rank": 5, "user_id": "10005", "nickname": "阿花", "affection": 28, "level": "眼熟", "avatar": "https://q1.qlogo.cn/g?b=qq&nk=10005&s=640"},
        {"rank": 6, "user_id": "10006", "nickname": "大壮", "affection": 15, "level": "眼熟", "avatar": ""},
        {"rank": 7, "user_id": "10007", "nickname": "老王", "affection": 0, "level": "陌生", "avatar": "https://q1.qlogo.cn/g?b=qq&nk=10007&s=640"},
        {"rank": 8, "user_id": "10008", "nickname": "小黑", "affection": -15, "level": "陌生", "avatar": ""},
        {"rank": 9, "user_id": "10009", "nickname": "路人甲", "affection": -60, "level": "仇视", "avatar": "https://q1.qlogo.cn/g?b=qq&nk=10009&s=640"},
        {"rank": 10, "user_id": "10010", "nickname": "路人乙", "affection": -88, "level": "仇视", "avatar": ""},
    ],
}


async def render_png():
    payload = {
        "tmpl": TMPL,
        "json": False,  # 返回图片文件而非 URL
        "tmpldata": TMPL_DATA,
        "options": {
            "viewport_width": 700,
            "viewport_height": 1,
            "type": "png",
            "full_page": True,
        },
    }

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(trust_env=True, connector=connector) as session:
        url = f"{T2I_BASE}/generate"
        print(f"POST {url}")
        async with session.post(url, json=payload) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                if resp.content_type.startswith("image/"):
                    data = await resp.read()
                    out = "rendered_ranking.png"
                    with open(out, "wb") as f:
                        f.write(data)
                    print(f"Saved {len(data)} bytes to {out}")
                else:
                    text = await resp.text()
                    print(f"Response: {text[:500]}")

                    # 如果返回 JSON URL，下载它
                    try:
                        ret = json.loads(text)
                        img_url = f"{T2I_BASE}/{ret['data']['id']}"
                        print(f"Image URL: {img_url}")
                        async with session.get(img_url) as img_resp:
                            if img_resp.status == 200:
                                data = await img_resp.read()
                                out = "rendered_ranking.png"
                                with open(out, "wb") as f:
                                    f.write(data)
                                print(f"Downloaded and saved to {out}")
                    except Exception:
                        pass
            else:
                text = await resp.text()
                print(f"Error: {text[:500]}")


async def main():
    await render_png()


if __name__ == "__main__":
    asyncio.run(main())
