"""
access_key 适配层 —— 仅作备用凭证源
====================================
设计原则：
1. 主流程是 QrCodeLogin 扫码 + 浏览器 cookie，本模块不介入
2. access_key 无法被 bilibili_api.Credential 直接消费，必须兑换成 cookie
3. 兑换链路非官方，随时可能失效；失败就报错，绝不让程序静默用错凭证
4. 任何凭证都不允许硬编码、不允许提交 git
"""

import os
import json
import asyncio
import aiohttp

BRIDGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "access_key.json")


def _load_raw_access_key() -> str | None:
    """读取本地保存的 access_key。仅读取，不存储明文到日志。"""
    if not os.path.exists(BRIDGE_FILE):
        return None
    try:
        with open(BRIDGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = data.get("access_key") or data.get("accessKey") or data.get("key")
        return key.strip() if key else None
    except Exception as e:
        print(f"[access_key] 读取失败: {e}")
        return None


async def _exchange_access_key_to_cookies(access_key: str) -> dict:
    """
    用 access_key 兑换 cookie 字段。
    ⚠️ 此接口为逆向所得，非官方，随时可能失效。
    若 B 站改版导致 412 / -101，说明链路已断，请用扫码登录。
    """
    url = "https://passport.bilibili.com/x/passport-login/oauth2/refresh_token"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
        "Origin": "https://www.bilibili.com",
    }
    payload = {
        "access_key": access_key,
        "refresh_token": "",   # logintool 若未提供 refresh_token，此处留空，成功率会低
        "source": "main_web",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=15) as resp:
            resp.raise_for_status()
            data = await resp.json()

    if data.get("code") != 0:
        raise RuntimeError(
            f"access_key 兑换失败: code={data.get('code')} message={data.get('message')}"
        )

    # 兑换成功通常返回新的 cookie 字段；结构随版本变化，做容错
    cookie_info = (data.get("data") or {}).get("cookie_info") or data.get("data") or {}
    cookies = cookie_info.get("cookies") or []
    result = {}
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name and value is not None:
            result[name] = value

    if not result.get("SESSDATA"):
        raise RuntimeError("兑换成功但未拿到 SESSDATA，链路可能已失效")

    return result


async def access_key_to_credential() -> "Credential | None":
    """
    尝试把 access_key 转换成 Credential。
    返回 None 表示没有 access_key 文件或兑换失败 —— 调用方应回退扫码登录。
    """
    from bilibili_api import Credential

    access_key = _load_raw_access_key()
    if not access_key:
        return None

    print("[access_key] 检测到 access_key.json，尝试兑换为登录态 ...")
    try:
        cookies = await _exchange_access_key_to_cookies(access_key)
    except Exception as e:
        print(f"[access_key] 兑换失败，将回退扫码登录: {e}")
        return None

    # 日志里只打印字段名，不打印值
    print(f"[access_key] 兑换成功，获得字段: {list(cookies.keys())}")

    return Credential(
        sessdata=cookies.get("SESSDATA", ""),
        bili_jct=cookies.get("bili_jct", ""),
        buvid3=cookies.get("buvid3", ""),
        dedeuserid=cookies.get("DedeUserID"),
        ac_time_value=cookies.get("ac_time_value"),
    )


def save_access_key(access_key: str, refresh_token: str = "") -> None:
    """
    保存 access_key 到本地。仅供手动导入场景使用。
    ⚠️ access_key.json 必须进 .gitignore
    """
    with open(BRIDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"access_key": access_key, "refresh_token": refresh_token},
            f, indent=2, ensure_ascii=False
        )
    os.chmod(BRIDGE_FILE, 0o600)  # 仅本人可读写
    print(f"[access_key] 已保存至 {BRIDGE_FILE}（请勿提交 git）")
