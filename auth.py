"""
登录模块 —— 主流程
==================
优先级：
1. 本地 credential.json 存在且有效        → 直接加载（自动续期）
2. access_key.json 存在                  → 尝试兑换为 Credential（备用）
3. 环境变量 BILI_SESSDATA / BILI_JCT     → 手动 cookie 兜底
4. 以上都没有                            → QrCodeLogin 扫码

所有凭证均落盘到 .gitignore 中的文件，禁止硬编码。
"""

import os
import asyncio
from bilibili_api import Credential, login_v2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = os.path.join(BASE_DIR, "credential.json")


async def _build_from_env() -> Credential | None:
    sess = os.getenv("BILI_SESSDATA")
    jct = os.getenv("BILI_JCT")
    if not (sess and jct):
        return None
    return Credential(
        sessdata=sess,
        bili_jct=jct,
        buvid3=os.getenv("BILI_BUVID3", ""),
        dedeuserid=os.getenv("BILI_DEDEUSERID", "") or None,
        ac_time_value=os.getenv("BILI_AC_TIME", "") or None,
    )


async def _load_local() -> Credential | None:
    if not os.path.exists(CRED_FILE):
        return None
    try:
        cred = Credential.load(CRED_FILE)
    except Exception as e:
        print(f"[登录] credential.json 损坏: {e}")
        return None

    # 尝试静默续期
    try:
        need = await cred.check_refresh()
        if need:
            await cred.refresh()
            cred.save(CRED_FILE)
            print("[登录] cookie 已自动续期")
    except Exception as e:
        print(f"[登录] 续期失败，继续使用本地凭证: {e}")
    return cred


async def _login_qrcode() -> Credential:
    print("[登录] 请用 B 站 APP 扫描二维码 ...")
    qr = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)
    await qr.generate_qrcode()
    try:
        print(qr.get_qrcode_terminal())
    except Exception:
        pass
    while not qr.has_done():
        await qr.check_state()
        await asyncio.sleep(1)
    cred = qr.get_credential()
    cred.save(CRED_FILE)
    print("[登录] 扫码成功，已保存登录态")
    return cred


async def get_credential() -> Credential:
    # 1. 本地已有登录态
    cred = await _load_local()
    if cred is not None:
        print("[登录] 已加载本地登录态")
        return cred

    # 2. access_key 桥接（备用凭证源）
    try:
        from access_key_bridge import access_key_to_credential
        cred = await access_key_to_credential()
        if cred is not None:
            try:
                cred.save(CRED_FILE)
                print("[登录] 已由 access_key 生成并保存登录态")
            except Exception as e:
                print(f"[登录] 保存失败: {e}")
            return cred
    except Exception as e:
        print(f"[登录] access_key 桥接失败，回退扫码: {e}")

    # 3. 环境变量手动 cookie
    cred = await _build_from_env()
    if cred is not None:
        try:
            cred.save(CRED_FILE)
            print("[登录] 已用环境变量 cookie 保存登录态")
            return cred
        except Exception as e:
            print(f"[登录] 保存失败，返回临时凭证: {e}")
            return cred

    # 4. 扫码
    return await _login_qrcode()


def logout() -> None:
    if os.path.exists(CRED_FILE):
        os.remove(CRED_FILE)
        print("已退出登录")
