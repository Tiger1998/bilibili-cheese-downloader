#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili-cheese-downloader v4.01 (access_key 主线 + Web Cookie 自动切换版)
==================================================================
设计原则：
  1. 鉴权只用 access_key 一条主线，登录方式前置（扫码 / 手动 access_key / 已保存）
  2. 不依赖 bili_jct、不调用 refresh()、不读 Web Cookie，避免扫码后 SESSDATA 为空的问题
  3. get_list() 返回空时，自动提示切换到 Web Cookie 模式（一次兜底）
  4. 保留 v3.2 的完整下载流程：选集、并行、断点续传、ffmpeg 合成、多方案重试、GPU/H265/帧率转换
"""
import sys
import traceback
import json
from os import path, makedirs, remove, listdir
from subprocess import run, Popen, PIPE, STDOUT, TimeoutExpired
from time import sleep
from json import loads, dump as _json_dump
from re import compile
from uuid import uuid4
from tqdm import tqdm
from asyncio import create_task, gather, Semaphore, run as asyncio_run, sleep as asyncio_sleep
from concurrent.futures import ThreadPoolExecutor
from logging import basicConfig, FileHandler, StreamHandler, getLogger, INFO, WARNING, ERROR
from configparser import ConfigParser
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ============ 模块级全局变量 ============
NVIDIA_GPU_SUPPORTED = None
FORCE_GPU_MODE = None
logger = None

# ============ 0. Python 版本检查 ============
def _check_python():
    if sys.version_info < (3, 8):
        print("=" * 60)
        print("错误：Python 版本过低")
        print(f"当前版本：{sys.version}")
        print("本项目需要 Python 3.8 及以上（推荐 3.9 ~ 3.11）")
        print("=" * 60)
        return False
    if sys.version_info >= (3, 12):
        print("=" * 60)
        print("提示：检测到 Python 3.12+，已按异步 API 适配")
        print("=" * 60)
    return True

# ============ 1. 依赖自检（打包环境跳过） ============
def _check_dependencies():
    if getattr(sys, "frozen", False):
        return True, []
    missing = []
    for name, pkg in [("tqdm", "tqdm"), ("aiohttp", "aiohttp"),
                      ("bilibili_api", "bilibili-api-python"), ("yaml", "pyyaml")]:
        try:
            __import__(name)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("=" * 60)
        print("错误：缺少依赖库：" + ", ".join(missing))
        print("请执行：pip install " + " ".join(missing))
        print("=" * 60)
        return False, missing
    return True, []

# ============ 2. ffmpeg 自检 ============
def _check_ffmpeg():
    import shutil
    if not shutil.which("ffmpeg"):
        print("=" * 60)
        print("错误：未找到 ffmpeg，请安装并加入 PATH")
        print("安装指南：https://ffmpeg.org/download.html")
        print("=" * 60)
        return False
    if not shutil.which("ffprobe"):
        print("警告：未找到 ffprobe，合成/转码可能受影响")
    return True

# ============ 3. bilibili_api 导入兼容 ============
def _patch_bilibili_api_imports():
    import importlib
    import bilibili_api

    mod = None
    for nm in ("bilibili_api.login_v2", "bilibili_api.login"):
        try:
            mod = importlib.import_module(nm)
            break
        except Exception:
            continue

    if mod is None:
        print("错误：无法导入 bilibili_api 登录模块")
        return None

    QrCodeLogin = getattr(mod, "QrCodeLogin", None) or getattr(mod, "QRCodeLogin", None)
    QrCodeLoginChannel = getattr(mod, "QrCodeLoginChannel", None) or getattr(mod, "QRCodeLoginChannel", None)
    QrCodeLoginEvents = getattr(mod, "QrCodeLoginEvents", None)

    Credential = getattr(bilibili_api, "Credential", None)
    cheese = getattr(bilibili_api, "cheese", None)
    video = getattr(bilibili_api, "video", None)

    if cheese is None or Credential is None:
        print("错误：bilibili_api 缺少 cheese / Credential 模块")
        return None

    return {
        "login_mod": mod,
        "QrCodeLogin": QrCodeLogin,
        "QrCodeLoginChannel": QrCodeLoginChannel,
        "QrCodeLoginEvents": QrCodeLoginEvents,
        "Credential": Credential,
        "cheese": cheese,
        "video": video,
    }

# ============ 4. 鉴权（access_key 主线） ============
class Auth:
    SESSION_FILE = "bilibili.session"

    @staticmethod
    async def _await_if_coro(obj):
        if hasattr(obj, "__await__"):
            return await obj
        return obj

    @staticmethod
    def _read_access_key():
        # 优先 credentials.yaml，其次 bilibili.session
        for p, key in [("credentials.yaml", "access_key"), ("bilibili.session", "access_key")]:
            try:
                import yaml
                if p.endswith(".yaml"):
                    d = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
                    if d.get("access_key"):
                        return d["access_key"]
            except Exception:
                try:
                    d = json.loads(Path(p).read_text(encoding="utf-8"))
                    if d.get("access_key"):
                        return d["access_key"]
                except Exception:
                    continue
        return None

    @staticmethod
    async def load_credential(Credential):
        ak = Auth._read_access_key()
        if ak:
            logger.info("[OK] 从本地加载 access_key")
            return Credential(access_key=ak)
        return None

    @staticmethod
    async def qrcode_login(QrCodeLogin, QrCodeLoginChannel, QrCodeLoginEvents, Credential):
        qr = QrCodeLogin(platform=QrCodeLoginChannel.WEB)
        await Auth._await_if_coro(qr.generate_qrcode())
        try:
            print(qr.get_qrcode_terminal())
        except Exception:
            pass
        print("请使用 B 站 APP 扫描二维码...\n")

        while True:
            state = await Auth._await_if_coro(qr.check_state())
            if QrCodeLoginEvents:
                if state == QrCodeLoginEvents.SCAN:
                    print("  已扫码，请在手机确认...")
                elif state == QrCodeLoginEvents.CONF:
                    print("  已确认，正在获取凭证...")
                elif state == QrCodeLoginEvents.TIMEOUT:
                    print("[!] 二维码超时，请重新运行")
                    return None
                elif state == QrCodeLoginEvents.DONE:
                    break
            else:
                if qr.has_done():
                    break
            await asyncio_sleep(2)

        cred = qr.get_credential()
        ak = getattr(cred, "access_key", "") or ""
        if ak:
            Auth._save_access_key(ak)
            logger.info("[OK] access_key 已保存")
            return Credential(access_key=ak)
        logger.error("[!] 未获取到 access_key")
        return None

    @staticmethod
    def _save_access_key(ak):
        try:
            with open(Auth.SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({"access_key": ak}, f, ensure_ascii=False, indent=2)
            logger.info("access_key 已存盘")
        except Exception as e:
            logger.warning(f"存盘失败（忽略）: {e}")

    @staticmethod
    def manual_access_key(Credential):
        try:
            ak = input("access_key: ").strip()
        except EOFError:
            return None
        if not ak:
            return None
        Auth._save_access_key(ak)
        return Credential(access_key=ak)

    @staticmethod
    def manual_cookie_login(Credential):
        """access_key 拉不到列表时的兜底：手动填 Web Cookie"""
        print("\n" + "=" * 60)
        print("  access_key 无法获取付费课程列表")
        print("  请手动填入浏览器 Web Cookie")
        print("  浏览器 F12 → Application → Cookies → bilibili.com")
        print("=" * 60)
        try:
            sessdata = input("SESSDATA: ").strip()
            if not sessdata:
                return None
            bili_jct = input("bili_jct: ").strip()
            if not bili_jct:
                return None
            dedeuserid = input("DedeUserID (可回车): ").strip()
            buvid3 = input("buvid3 (可回车): ").strip()
        except EOFError:
            return None

        cred = Credential(sessdata=sessdata, bili_jct=bili_jct,
                          dedeuserid=dedeuserid, buvid3=buvid3)
        try:
            data = {"SESSDATA": sessdata, "bili_jct": bili_jct,
                    "DedeUserID": dedeuserid, "buvid3": buvid3}
            with open(Auth.SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            import yaml
            with open("credentials.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump({k.lower(): v for k, v in data.items()},
                               f, allow_unicode=True)
        except Exception as e:
            logger.warning(f"存盘失败: {e}")
        return cred

# ============ 5. 真正的主程序 ============
def _run_real_main(ctx):
    global logger, NVIDIA_GPU_SUPPORTED, FORCE_GPU_MODE

    Credential = ctx["Credential"]
    cheese = ctx["cheese"]
    video_mod = ctx["video"]
    QrCodeLogin = ctx["QrCodeLogin"]
    QrCodeLoginChannel = ctx["QrCodeLoginChannel"]
    QrCodeLoginEvents = ctx["QrCodeLoginEvents"]

    basicConfig(
        level=INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[FileHandler("bdownloader.log", encoding='utf-8'), StreamHandler()]
    )
    logger = getLogger("BDownloader")

    # ---- ffmpeg 工具 ----
    def detect_video_codec(video_path):
        try:
            r = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                     '-show_entries', 'stream=codec_name',
                     '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                    stdout=PIPE, stderr=PIPE, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def check_h265_support(use_gpu):
        try:
            r = run(['ffmpeg', '-encoders'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
            encs = r.stdout.lower()
            return any(e in encs for e in ['hevc_nvenc', 'libx265', 'hevc_vaapi'])
        except Exception:
            return False

    def _app_path(rel):
        if getattr(sys, "frozen", False):
            return str(Path(sys.executable).parent / rel)
        return './' + rel

    def ensure_dirs():
        for d in ['./download/temp', './download/failed']:
            makedirs(d, exist_ok=True)

    TIME_PATTERN = compile(r'(\d{2}):(\d{2}):(\d{2})')
    ILLEGAL_FILENAME_CHARS = compile(r'[<>:"/\\|?*]')

    def load_config():
        config = ConfigParser()
        config_file = Path('./config.ini')
        default_config = {
            'General': {
                'concurrent_downloads': '2',
                'concurrent_ffmpeg': '1',
                'gpu_mode': 'auto',
                'convert_framerate': 'false',
                'target_framerate': '30',
                'max_retries': '3',
                'retry_delay': '5',
                'convert_to_h265': 'false',
                'auto_switch_on_empty': 'true',
            }
        }
        for section, options in default_config.items():
            if not config.has_section(section):
                config.add_section(section)
            for key, value in options.items():
                if not config.has_option(section, key):
                    config.set(section, key, value)
        if not config_file.exists():
            with open(config_file, 'w', encoding='utf-8') as f:
                config.write(f)
        else:
            config.read(config_file, encoding='utf-8')
            dirty = False
            for section, options in default_config.items():
                if not config.has_section(section):
                    config.add_section(section); dirty = True
                for key, value in options.items():
                    if not config.has_option(section, key):
                        config.set(section, key, value); dirty = True
            if dirty:
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)
        return config

    def check_nvidia_gpu_support(force_mode=None):
        global NVIDIA_GPU_SUPPORTED, FORCE_GPU_MODE
        if force_mode is not None:
            FORCE_GPU_MODE = force_mode
            NVIDIA_GPU_SUPPORTED = force_mode
            return force_mode
        if NVIDIA_GPU_SUPPORTED is not None:
            return NVIDIA_GPU_SUPPORTED
        try:
            r = run(['ffmpeg', '-encoders'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
            nvenc = 'h264_nvenc' in r.stdout.lower()
            NVIDIA_GPU_SUPPORTED = nvenc
            return nvenc
        except Exception:
            NVIDIA_GPU_SUPPORTED = False
            return False

    def detect_video_framerate(video_path):
        try:
            r = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                     '-show_entries', 'stream=r_frame_rate',
                     '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                    stdout=PIPE, stderr=PIPE, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                p = r.stdout.strip().split('/')
                if len(p) == 2:
                    return round(float(p[0]) / float(p[1]), 1)
                return float(r.stdout.strip())
        except Exception:
            pass
        return None

    def parse_time_2_sec(s):
        m = TIME_PATTERN.search(s or "")
        if not m:
            return 0
        h, m2, s2 = m.groups()
        return int(h) * 3600 + int(m2) * 60 + int(s2)

    class ProgressManager:
        def __init__(self):
            self.bars = {}
        def create_bar(self, key, total, desc, position=0, unit='B'):
            bar = tqdm(total=total, unit=unit, unit_scale=True, desc=desc,
                       dynamic_ncols=True, position=position, leave=False)
            self.bars[key] = bar
            return bar
        def update_bar(self, key, value):
            if key in self.bars:
                self.bars[key].update(value)
        def close_bar(self, key):
            if key in self.bars:
                self.bars[key].close()
                del self.bars[key]
        def close_all(self):
            for bar in list(self.bars.values()):
                try:
                    bar.close()
                except Exception:
                    pass
            self.bars.clear()

    progress_mgr = ProgressManager()

    async def download_file(url, save_path, desc, task_index, total_tasks, task_type,
                            max_retries=3, retry_delay=5):
        from aiohttp import ClientSession
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        bar_key = f"dl_{task_index}_{task_type}"
        retry = 0
        while retry < max_retries:
            try:
                downloaded = path.getsize(save_path) if path.exists(save_path) else 0
                async with ClientSession() as session:
                    async with session.head(url, headers=headers) as resp:
                        file_size = int(resp.headers.get('content-length', 0))
                    if downloaded == file_size and file_size > 0:
                        return True
                    makedirs(path.dirname(save_path), exist_ok=True)
                    progress_mgr.create_bar(bar_key, file_size,
                                            f'[{task_index}/{total_tasks}] {desc} {task_type}',
                                            task_index % 10)
                    if downloaded > 0:
                        progress_mgr.bars[bar_key].update(downloaded)
                        headers['Range'] = f'bytes={downloaded}-'
                    mode = 'ab' if downloaded > 0 else 'wb'
                    async with session.get(url, headers=headers) as resp:
                        if resp.status not in (200, 206):
                            raise Exception(f"HTTP {resp.status}")
                        with open(save_path, mode) as f:
                            async for chunk in resp.content.iter_chunked(32768):
                                if chunk:
                                    f.write(chunk)
                                    progress_mgr.update_bar(bar_key, len(chunk))
                progress_mgr.close_bar(bar_key)
                return True
            except Exception:
                retry += 1
                if retry >= max_retries:
                    progress_mgr.close_bar(bar_key)
                    raise
                await asyncio_sleep(retry_delay)

    def _pick_url(stream_info):
        """兼容 base_url / baseUrl / url"""
        if not isinstance(stream_info, dict):
            return ""
        for key in ("base_url", "baseUrl", "url"):
            v = stream_info.get(key, "")
            if v:
                return v
        return ""

    def sanitize_filename(filename):
        return ILLEGAL_FILENAME_CHARS.sub('_', filename).strip()

    def build_ffmpeg_cmd(video_file, audio_file, output_file, use_gpu=False,
                         convert_to_h265=False, convert_framerate=False,
                         target_framerate=30, original_framerate=None, attempt=0):
        vf = []
        if convert_framerate and original_framerate:
            vf.append(f"fps={target_framerate}")
        vf_args = f'-vf "{",".join(vf)}"' if vf else ""
        vc = "libx265" if convert_to_h265 else "copy"
        if convert_to_h265 and use_gpu:
            vc = "hevc_nvenc"
        map_args = '-map 0:v:0 -map 1:a:0 -shortest'
        base = f'ffmpeg -y -i "{video_file}" -i "{audio_file}"'
        out = f'"{output_file}"'
        opts = [
            f'{base} -c:v copy -c:a copy {map_args} {out}',
            f'{base} {vf_args} -c:v {vc} -c:a copy {map_args} {out}',
            f'{base} {vf_args} -c:v {vc} -preset fast -c:a copy {map_args} {out}',
        ]
        return opts[min(attempt, len(opts) - 1)]

    def ffmpeg_merge(video_file, audio_file, output_file, title, index, total_count,
                    duration, convert_to_h265=False, convert_framerate=False,
                    target_framerate=30, original_framerate=None):
        bar_key = f"ff_{index}"
        bar = progress_mgr.create_bar(bar_key, duration,
                                      f'[{index}/{total_count}] {title} [3/3]',
                                      index % 10, unit='second')
        attempt = 0
        while attempt < 5:
            cmd = build_ffmpeg_cmd(video_file, audio_file, output_file,
                                   use_gpu=bool(NVIDIA_GPU_SUPPORTED),
                                   convert_to_h265=convert_to_h265,
                                   convert_framerate=convert_framerate,
                                   target_framerate=target_framerate,
                                   original_framerate=original_framerate,
                                   attempt=attempt)
            proc = Popen(cmd, stdout=PIPE, stderr=STDOUT, universal_newlines=True, shell=True)
            for line in proc.stdout:
                if 'size=' in line:
                    t = parse_time_2_sec(line)
                    progress_mgr.update_bar(bar_key, t - bar.n)
            if proc.wait() == 0 and path.exists(output_file) and path.getsize(output_file) > 0:
                progress_mgr.close_bar(bar_key)
                for tmp in (audio_file, video_file):
                    try:
                        remove(tmp)
                    except Exception:
                        pass
                return True
            attempt += 1
        progress_mgr.close_bar(bar_key)
        return False

    def cleanup_temp_dir():
        if path.exists('./download/temp'):
            for f in listdir('./download/temp'):
                try:
                    remove(path.join('./download/temp', f))
                except Exception:
                    pass

    async def process_episode(ep, position_index, total_count, semaphore, course_folder,
                              original_index, convert_to_h265=False, convert_framerate=False,
                              target_framerate=30, max_retries=3, retry_delay=5, credential=None):
        prefix = f"{original_index}_{uuid4().hex}"
        audio_file = f"./download/temp/{prefix}_audio.m4s"
        video_file = f"./download/temp/{prefix}_video.m4s"
        try:
            async with semaphore:
                ep_id = ep.get_epid()
                meta = await ep.get_meta()
                title = meta.get('title', f'第{original_index}集')
                safe = sanitize_filename(title)
                output_file = f"./download/{course_folder}/{original_index:03d}_{safe}.mp4"
                makedirs(f"./download/{course_folder}", exist_ok=True)

                playurl = await ep.get_download_url()

                # 选流
                streams = None
                try:
                    detector = video_mod.VideoDownloadURLDataDetecter(data=playurl)
                    streams = detector.detect_best_streams()
                except Exception as e:
                    logger.warning(f"detect_best_streams 失败，手动选流: {e}")

                if not streams:
                    dash = (playurl.get("dash") or
                            (playurl.get("data") or {}).get("dash") or {})
                    v_streams = sorted(dash.get("video", []), key=lambda s: s.get("id", 0), reverse=True)
                    a_streams = sorted(dash.get("audio", []), key=lambda s: s.get("id", 0), reverse=True)
                    if not v_streams or not a_streams:
                        raise Exception("音视频流为空")
                    from types import SimpleNamespace
                    streams = [SimpleNamespace(url=_pick_url(v_streams[0])),
                               SimpleNamespace(url=_pick_url(a_streams[0]))]

                await download_file(streams[1].url, audio_file, safe[:20],
                                    position_index, total_count, "audio [1/3]",
                                    max_retries, retry_delay)
                await download_file(streams[0].url, video_file, safe[:20],
                                    position_index, total_count, "video [2/3]",
                                    max_retries, retry_delay)

                if not path.exists(audio_file) or path.getsize(audio_file) == 0:
                    raise Exception("音频下载失败或为空")

                duration = meta.get('duration', 0)
                fr = detect_video_framerate(video_file) if convert_framerate else None
                ok = ffmpeg_merge(video_file, audio_file, output_file, safe[:20],
                                  position_index, total_count, duration,
                                  convert_to_h265, convert_framerate,
                                  target_framerate, fr)
                if ok:
                    logger.info(f"[OK] 第 {original_index} 集：{safe}")
                else:
                    logger.error(f"[FAIL] 第 {original_index} 集合成失败")
                return {"success": ok, "original_index": original_index}
        except Exception as e:
            logger.error(f"[FAIL] 第 {original_index} 集：{e}")
            for tmp in (audio_file, video_file):
                try:
                    if path.exists(tmp):
                        remove(tmp)
                except Exception:
                    pass
            return {"success": False, "original_index": original_index, "error": str(e)}

    def safe_input(prompt=""):
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            raise

    async def login_flow(Credential):
        """登录前置：扫码 / 手动 access_key / 已保存"""
        print("\n== 登录方式 ==")
        print("  1. 扫码登录（推荐）")
        print("  2. 手动填 access_key")
        print("  3. 使用已保存的 access_key")
        c = safe_input("选择 (1/2/3，默认1): ").strip() or "1"

        cred = None
        if c == "3":
            cred = await Auth.load_credential(Credential)
            if cred is None:
                print("[!] 无已保存凭证，改为扫码")
                c = "1"
        if c == "2":
            cred = Auth.manual_access_key(Credential)
        if c == "1":
            cred = await Auth.qrcode_login(QrCodeLogin, QrCodeLoginChannel,
                                          QrCodeLoginEvents, Credential)
        return cred

    async def main():
        ensure_dirs()
        cleanup_temp_dir()

        config = load_config()
        convert_to_h265 = config.getboolean('General', 'convert_to_h265', fallback=False)
        convert_framerate = config.getboolean('General', 'convert_framerate', fallback=False)
        target_framerate = config.getint('General', 'target_framerate', fallback=30)
        max_retries = config.getint('General', 'max_retries', fallback=3)
        retry_delay = config.getint('General', 'retry_delay', fallback=5)
        concurrent = config.getint('General', 'concurrent_downloads', fallback=2)
        auto_switch = config.getboolean('General', 'auto_switch_on_empty', fallback=True)

        # 选项交互
        c = safe_input("\n开启帧率转换？1=是 2=否 (默认2): ").strip()
        convert_framerate = c == '1'
        if convert_framerate:
            try:
                t = safe_input(f"目标帧率 (默认{target_framerate}): ").strip()
                if t:
                    target_framerate = int(t)
            except ValueError:
                pass

        c = safe_input("开启 H265 转换？1=是 2=否 (默认2): ").strip()
        convert_to_h265 = c == '1'
        if convert_to_h265:
            check_h265_support(bool(NVIDIA_GPU_SUPPORTED))

        c = safe_input("GPU 加速：1=自动 2=强制GPU 3=强制CPU (默认1): ").strip()
        check_nvidia_gpu_support(True if c == '2' else False if c == '3' else None)

        # 登录前置
        credential = await login_flow(Credential)
        if credential is None:
            print("[错误] 登录失败，退出")
            return

        print("\n== 课程信息 ==")
        input_id = safe_input('输入课程ID（如 ss656277622）: ').strip()
        if not input_id:
            return

        if input_id.startswith('ss'):
            season_id = int(input_id[2:])
        else:
            season_id = int(input_id)

        cheese_list = cheese.CheeseList(season_id=season_id, credential=credential)

        try:
            course_info = await cheese_list.get_meta()
        except Exception as e:
            logger.error(f"获取课程信息失败: {e}")
            print(f"[错误] 获取课程信息失败: {e}")
            return

        course_title = sanitize_filename(course_info.get('title', f'course_{season_id}'))
        print(f"课程：{course_info.get('title')}")

        episodes = await cheese_list.get_list()
        print(f"get_list() 返回 {len(episodes)} 集")

        # ★ 空列表自动切换 Web Cookie
        if not episodes and auto_switch:
            print("\n" + "!" * 60)
            print("  [!] access_key 模式下分集列表为空")
            print("  [!] 付费课程需要 Web Cookie (SESSDATA)")
            print("!" * 60)
            c = safe_input("\n是否切换到 Web Cookie 模式？(Y/n，默认Y): ").strip().lower()
            if c != 'n':
                cred2 = Auth.manual_cookie_login(Credential)
                if cred2 is not None:
                    credential = cred2
                    cheese_list = cheese.CheeseList(season_id=season_id, credential=credential)
                    try:
                        course_info = await cheese_list.get_meta()
                        episodes = await cheese_list.get_list()
                        print(f"[重试] get_list() 返回 {len(episodes)} 集")
                    except Exception as e:
                        logger.error(f"切换后仍失败: {e}")
                        print(f"[错误] 切换后仍然失败: {e}")
                        return
                else:
                    print("[取消] 未输入 Cookie，退出")
                    return

        if not episodes:
            print("[错误] 分集列表仍为空，退出")
            print("  提示：请确认课程已购买，或 SESSDATA 未过期")
            return

        print(f"课程共 {len(episodes)} 集")

        # 选集
        print("\n[1] 全部下载  [2] 指定集数  [3] 范围")
        c = safe_input("选择 (默认1): ").strip() or "1"
        selected = []
        if c == '1':
            selected = [(i + 1, ep) for i, ep in enumerate(episodes)]
        elif c == '2':
            nums = safe_input("输入集数（逗号分隔，如 1,3,5）: ").strip()
            for n in nums.split(','):
                try:
                    idx = int(n.strip()) - 1
                    if 0 <= idx < len(episodes):
                        selected.append((idx + 1, episodes[idx]))
                except ValueError:
                    pass
        elif c == '3':
            s = int(safe_input("起始: ").strip())
            e = int(safe_input("结束: ").strip())
            for i in range(s, min(e + 1, len(episodes) + 1)):
                selected.append((i, episodes[i - 1]))

        if not selected:
            print("未选择任何集")
            return

        print(f"\n将下载 {len(selected)} 集")

        try:
            cc = safe_input(f"并行数 (默认{concurrent}): ").strip()
            if cc:
                concurrent = min(int(cc), len(selected))
        except ValueError:
            pass

        semaphore = Semaphore(concurrent)
        tasks = []
        for i, (orig_idx, ep) in enumerate(selected, 1):
            tasks.append(create_task(process_episode(
                ep, i, len(selected), semaphore, course_title,
                orig_idx, convert_to_h265, convert_framerate,
                target_framerate, max_retries, retry_delay, credential
            )))

        results = await gather(*tasks)
        ok = sum(1 for r in results if r.get("success"))
        fail = len(results) - ok
        print(f"\n完成：成功 {ok}，失败 {fail}")

        progress_mgr.close_all()
        cleanup_temp_dir()

    return asyncio_run(main())


# ============ 启动入口 ============
def _pause_before_exit():
    if not sys.stdout.isatty():
        try:
            input("\n按回车退出...")
        except Exception:
            pass

def main_entry():
    print("=" * 60)
    print("  bilibili-cheese-downloader v4.01")
    print("  access_key 主线 + Web Cookie 自动切换")
    print("=" * 60)

    if not _check_python():
        _pause_before_exit(); sys.exit(1)
    ok, _ = _check_dependencies()
    if not ok:
        _pause_before_exit(); sys.exit(1)
    if not _check_ffmpeg():
        _pause_before_exit(); sys.exit(1)

    ctx = _patch_bilibili_api_imports()
    if ctx is None:
        _pause_before_exit(); sys.exit(1)

    print("[自检通过] 开始运行...\n")
    _run_real_main(ctx)

if __name__ == "__main__":
    try:
        main_entry()
    except KeyboardInterrupt:
        print("\n[用户中断]")
    except Exception:
        print("\n程序异常：")
        traceback.print_exc()
    finally:
        _pause_before_exit()
