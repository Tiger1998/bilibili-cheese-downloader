#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili-cheese-downloader 3.1 —— 全局变量 / AudioQuality / json.dump 综合修复版
============================================================
基于 3.0 闪退修复版，新增修复：
  A. 修复 NameError: name 'NVIDIA_GPU_SUPPORTED' is not defined
       - 根因：NVIDIA_GPU_SUPPORTED / FORCE_GPU_MODE 原在 _run_real_main() 内
         被定义为局部变量，而 check_nvidia_gpu_support() 用 global 声明要
         访问模块级变量，两边对不上。
       - 方案：将这两个变量提升为模块级全局变量，并在函数内用 global 声明。
  B. 修复 TypeError: dumps() takes 1 positional argument but 2...
       - 根因：脚本中 json.dumps(obj, f, ...) 把文件句柄当位置参数传入，
         dumps 只接受一个位置参数，写文件必须用 json.dump()。
       - 方案：将所有 dumps(obj, f, ...) 改为 dump(obj, f, ...)。
  C. 修复 ValueError: xxx is not a valid AudioQuality
       - 根因：B 站 API 返回了新的音质 ID（如 100010 Hi-Res），库内枚举未收录。
       - 方案：在 detect_best_streams() 调用处加 try/except，失败时 fallback
         手动选流，避免整集中断。
  D. 继续保留 3.0 的全部闪退防护：环境自检、config 补齐、EOFError 防护等。
"""
import sys
import traceback

# ============ 模块级全局变量（v3.1 修复 A） ============
# 解决 NameError: name 'NVIDIA_GPU_SUPPORTED' is not defined
# 原 3.0 把这些变量定义在 _run_real_main() 内部，成了局部变量；
# 而 check_nvidia_gpu_support() 用 global 声明要访问模块级变量，导致对不上。
# 现提升为模块级，函数内保留 global 声明即可正常工作。
NVIDIA_GPU_SUPPORTED = None   # None=未检测, True=有N卡, False=无
FORCE_GPU_MODE = None         # None=未强制, 'auto' / 'gpu' / 'cpu'

# ============ 0. 提前判断 Python 版本（在任何 import 之前） ============
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
        print("警告：检测到 Python 3.12 及以上")
        print("bilibili-api-python 部分老版本在 3.12+ 下有兼容性问题")
        print("建议：使用 Python 3.9 ~ 3.11，或升级 bilibili-api-python 到最新版")
        print("      pip install -U bilibili-api-python")
        print("=" * 60)
    return True


# ============ 1. 依赖自检 ============
def _check_dependencies():
    """返回 (ok, missing_list)"""
    missing = []
    try:
        import tqdm  # noqa: F401
    except ImportError:
        missing.append("tqdm")
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        missing.append("aiohttp")
    try:
        import bilibili_api  # noqa: F401
    except ImportError:
        missing.append("bilibili-api-python")

    ok = len(missing) == 0
    if not ok:
        print("=" * 60)
        print("错误：缺少依赖库")
        print("未安装：" + ", ".join(missing))
        print("请执行：")
        print(f"    pip install {' '.join(missing)}")
        print("=" * 60)
    return ok, missing


# ============ 2. ffmpeg / ffprobe 自检 ============
def _check_ffmpeg():
    import shutil
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    if not ff:
        print("=" * 60)
        print("错误：未找到 ffmpeg")
        print("ffmpeg 未安装或未加入 PATH")
        print("安装指南：https://ffmpeg.org/download.html")
        print("macOS：  brew install ffmpeg")
        print("Windows：下载解压后将 bin 目录加入 PATH")
        print("=" * 60)
        return False
    if not fp:
        print("=" * 60)
        print("警告：未找到 ffprobe（ffmpeg 通常自带，可能是 PATH 不完整）")
        print("合并/转码步骤可能失败")
        print("=" * 60)
    return True


# ============ 3. bilibili_api 内部导入兼容（修复 login_v2 路径问题） ============
def _patch_bilibili_api_imports():
    """
    兼容不同版本 bilibili_api 的 login 模块路径：
      新版本：bilibili_api.login_v2
      老版本：bilibili_api.login  (QrCodeLogin / QrCodeLoginChannel)
    同时兼容 Credential 所在位置。
    """
    import importlib
    import bilibili_api

    mod = None
    login_v2 = None
    # 先试新路径
    try:
        login_v2 = importlib.import_module("bilibili_api.login_v2")
        mod = login_v2
    except Exception:
        pass
    # 再试老路径
    if mod is None:
        try:
            login_v2 = importlib.import_module("bilibili_api.login")
            mod = login_v2
        except Exception:
            pass

    if mod is None:
        print("=" * 60)
        print("错误：无法导入 bilibili_api 的登录模块")
        print("当前 bilibili-api-python 版本可能过新或过旧，请尝试：")
        print("    pip install -U bilibili-api-python")
        print("或锁定版本：")
        print("    pip install bilibili-api-python==17.1.5")
        print("=" * 60)
        return None

    # 提取 QrCodeLogin / QrCodeLoginChannel
    QrCodeLogin = getattr(mod, "QrCodeLogin", None)
    QrCodeLoginChannel = getattr(mod, "QrCodeLoginChannel", None)
    # 部分老版本类名不同
    if QrCodeLogin is None:
        for name in ("QRCodeLogin", "QrcodeLogin", "qr_login"):
            QrCodeLogin = getattr(mod, name, None)
            if QrCodeLogin is not None:
                break
    if QrCodeLoginChannel is None:
        for name in ("QRCodeLoginChannel", "QrcodeLoginChannel", "QrCodeLoginPlatform"):
            QrCodeLoginChannel = getattr(mod, name, None)
            if QrCodeLoginChannel is not None:
                break

    if QrCodeLogin is None or QrCodeLoginChannel is None:
        print("=" * 60)
        print("错误：当前 bilibili_api 版本登录接口不兼容")
        print(f"已加载模块：{mod.__name__}")
        print("可用成员：" + ", ".join(dir(mod)))
        print("建议重新安装：pip install bilibili-api-python==17.1.5")
        print("=" * 60)
        return None

    Credential = getattr(bilibili_api, "Credential", None)
    cheese = getattr(bilibili_api, "cheese", None)
    video = getattr(bilibili_api, "video", None)
    sync = getattr(bilibili_api, "sync", None)

    if cheese is None:
        print("=" * 60)
        print("错误：bilibili_api 中没有 cheese 模块")
        print("当前安装版本可能不支持课程下载，请升级：")
        print("    pip install -U bilibili-api-python")
        print("=" * 60)
        return None

    return {
        "login_v2": mod,
        "QrCodeLogin": QrCodeLogin,
        "QrCodeLoginChannel": QrCodeLoginChannel,
        "Credential": Credential,
        "cheese": cheese,
        "video": video,
        "sync": sync,
    }


# ============ 真正的主程序（从原脚本迁移，仅修复问题点） ============
def _run_real_main(ctx):
    from os import path, makedirs, remove, listdir
    from subprocess import run, Popen, PIPE, STDOUT, TimeoutExpired
    from time import sleep
    from json import loads, dumps
    from re import compile
    from uuid import uuid4
    from tqdm import tqdm
    from threading import RLock, Lock
    from asyncio import create_task, gather, Semaphore, run as asyncio_run, CancelledError, sleep as asyncio_sleep
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from logging import basicConfig, FileHandler, StreamHandler, getLogger, INFO, ERROR, WARNING
    from configparser import ConfigParser
    from pathlib import Path
    import shutil

    Credential = ctx["Credential"]
    cheese = ctx["cheese"]
    video = ctx["video"]
    login_v2_mod = ctx["login_v2"]
    QrCodeLogin = ctx["QrCodeLogin"]
    QrCodeLoginChannel = ctx["QrCodeLoginChannel"]

    # 设置日志系统
    basicConfig(
        level=INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            FileHandler("bdownloader.log", encoding='utf-8'),
            StreamHandler()
        ]
    )
    logger = getLogger("BDownloader")

    # 注意：NVIDIA_GPU_SUPPORTED / FORCE_GPU_MODE 已在模块级定义（见文件顶部）
    # 本函数内通过 global 声明访问，详见 check_nvidia_gpu_support()

    # ---- ffmpeg 相关函数 ----
    def detect_video_codec(video_path):
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = run(cmd, stdout=PIPE, stderr=PIPE, text=True, timeout=10)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"检测视频编码失败: {result.stderr}")
                return None
        except TimeoutExpired:
            logger.error("检测视频编码超时")
            return None
        except Exception as e:
            logger.error(f"检测视频编码时出错: {e}")
            return None

    def check_h265_support(use_gpu):
        try:
            encoders_result = run(['ffmpeg', '-encoders'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
            encoders_output = encoders_result.stdout.lower()
            h265_encoders = [
                'hevc_nvenc', 'h265_nvenc',
                'hevc_vaapi', 'h265_vaapi',
                'hevc_amf', 'h265_amf',
                'libx265', 'hevc', 'h265'
            ]
            supported_encoders = [enc for enc in h265_encoders if enc in encoders_output]
            if supported_encoders:
                logger.info(f"检测到支持的H265编码器: {', '.join(supported_encoders)}")
            else:
                logger.warning("未检测到任何H265编码器支持")
            return len(supported_encoders) > 0
        except TimeoutExpired:
            logger.error("检测H265支持超时")
            return False
        except Exception as e:
            logger.error(f"检测H265支持时出错: {e}")
            return False

    def check_ffmpeg():
        try:
            result = run(['ffmpeg', '-version'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
            if result.returncode == 0:
                logger.info("FFmpeg 已正确安装")
                return True
            else:
                logger.error("FFmpeg 安装异常，无法正常运行")
                return False
        except FileNotFoundError:
            logger.error("未找到 FFmpeg")
            print("\n错误: FFmpeg 未安装或未添加到系统 PATH。")
            print("安装指南: https://ffmpeg.org/download.html")
            return False
        except TimeoutExpired:
            logger.error("FFmpeg检测超时")
            return False
        except Exception as e:
            logger.error(f"检测 FFmpeg 时出错: {e}")
            print(f"\n错误: 检测 FFmpeg 时出现问题: {e}")
            return False

    def ensure_dirs():
        if not path.exists('./download/temp'):
            makedirs('./download/temp')
        if not path.exists('./download/failed'):
            makedirs('./download/failed')

    PROGRESS_BARS = {}
    PROGRESS_BAR_LOCK = RLock()
    TIME_PATTERN = compile(r'(\d{2}):(\d{2}):(\d{2})')
    ILLEGAL_FILENAME_CHARS = compile(r'[<>:"/\\|?*]')

    # ---- 配置加载（修复：补齐 convert_to_h265 等可能缺失的键） ----
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
                'convert_to_h265': 'false',   # 修复：补上原脚本缺失的键
            }
        }
        if not config_file.exists():
            for section, options in default_config.items():
                if not config.has_section(section):
                    config.add_section(section)
                for key, value in options.items():
                    config.set(section, key, value)
            with open(config_file, 'w', encoding='utf-8') as f:
                config.write(f)
            logger.info(f"已创建默认配置文件: {config_file}")
        else:
            # 修复：已存在的 ini 可能缺键，补默认值
            config.read(config_file, encoding='utf-8')
            dirty = False
            for section, options in default_config.items():
                if not config.has_section(section):
                    config.add_section(section)
                    dirty = True
                for key, value in options.items():
                    if not config.has_option(section, key):
                        config.set(section, key, value)
                        dirty = True
            if dirty:
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)
                logger.info("已为已有 config.ini 补齐缺失配置项")

        return config

    def check_nvidia_gpu_support(force_mode=None):
        global NVIDIA_GPU_SUPPORTED, FORCE_GPU_MODE
        if force_mode is not None:
            FORCE_GPU_MODE = force_mode
            NVIDIA_GPU_SUPPORTED = force_mode
            mode_str = "GPU" if force_mode else "CPU"
            logger.warning(f"已手动设置为强制使用{mode_str}模式")
            return FORCE_GPU_MODE
        if NVIDIA_GPU_SUPPORTED is not None and FORCE_GPU_MODE is None:
            return NVIDIA_GPU_SUPPORTED
        if FORCE_GPU_MODE is not None:
            return FORCE_GPU_MODE
        try:
            logger.info("正在检测系统是否支持NVIDIA GPU加速...")
            encoders_output = ""
            try:
                encoders_result = run(['ffmpeg', '-encoders'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
                encoders_output = encoders_result.stdout.lower()
                nvenc_available = 'h264_nvenc' in encoders_output
                logger.info(f"- NVENC编码器检测: {'可用' if nvenc_available else '不可用'}")
            except TimeoutExpired:
                nvenc_available = False
                logger.error("- NVENC编码器检测超时")
            except Exception as e:
                nvenc_available = False
                logger.error(f"- NVENC编码器检测失败: {e}")

            hwaccels_output = ""
            try:
                hwaccels_result = run(['ffmpeg', '-hwaccels'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
                hwaccels_output = hwaccels_result.stdout.lower()
                cuda_available = 'cuda' in hwaccels_output
                logger.info(f"- CUDA硬件加速检测: {'可用' if cuda_available else '不可用'}")
            except TimeoutExpired:
                cuda_available = False
                logger.error("- CUDA硬件加速检测超时")
            except Exception as e:
                cuda_available = False
                logger.error(f"- CUDA硬件加速检测失败: {e}")

            nvidia_smi_available = False
            try:
                nvidia_smi_result = run(['nvidia-smi'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
                nvidia_smi_available = nvidia_smi_result.returncode == 0
                logger.info(f"- NVIDIA GPU检测: {'可用' if nvidia_smi_available else '不可用'}")
            except TimeoutExpired:
                nvidia_smi_available = False
                logger.info("- NVIDIA GPU检测超时")
            except Exception:
                nvidia_smi_available = False
                logger.info("- NVIDIA GPU检测: 不可用")

            NVIDIA_GPU_SUPPORTED = nvenc_available or (cuda_available and nvidia_smi_available)
            if NVIDIA_GPU_SUPPORTED:
                logger.info("检测到NVIDIA GPU加速支持")
            else:
                logger.info("未检测到可用的NVIDIA GPU加速，将使用CPU模式")
            return NVIDIA_GPU_SUPPORTED
        except Exception as e:
            logger.error(f"检测NVIDIA GPU支持时出错: {e}，将使用CPU模式")
            NVIDIA_GPU_SUPPORTED = False
            return False

    def detect_video_framerate(video_path):
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=r_frame_rate',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = run(cmd, stdout=PIPE, stderr=PIPE, text=True, timeout=10)
            if result.returncode == 0:
                frame_rate = result.stdout.strip()
                if not frame_rate:
                    return None
                if '/' in frame_rate:
                    num, den = frame_rate.split('/')
                    try:
                        return round(float(num) / float(den), 1)
                    except (ValueError, ZeroDivisionError):
                        return None
                try:
                    return float(frame_rate)
                except ValueError:
                    return None
            else:
                logger.error(f"检测视频帧率失败: {result.stderr}")
                return None
        except TimeoutExpired:
            logger.error("检测视频帧率超时")
            return None
        except Exception as e:
            logger.error(f"检测视频帧率时出错: {e}")
            return None

    def parse_time_2_sec(s):
        duration_match = TIME_PATTERN.search(s)
        if not duration_match:
            return 0
        hours, minutes, seconds_milliseconds = duration_match.groups()
        seconds = int(hours) * 3600 + int(minutes) * 60
        seconds += int(seconds_milliseconds)
        return seconds

    def format_title(title, max_length=20):
        if len(title) > max_length:
            return title[:max_length-3] + "..."
        else:
            return title.ljust(max_length)

    class ProgressManager:
        def __init__(self):
            self.bars = {}
            self.lock = RLock()

        def create_bar(self, key, total, desc, position=0, unit='B', leave=False):
            with self.lock:
                bar = tqdm(
                    total=total, unit=unit, unit_scale=True,
                    desc=desc, dynamic_ncols=True, position=position,
                    leave=leave, smoothing=0.01, mininterval=0.2,
                    maxinterval=1.0, miniters=None, ncols=100
                )
                self.bars[key] = bar
                return bar

        def update_bar(self, key, value):
            with self.lock:
                if key in self.bars and not self.bars[key].disable:
                    self.bars[key].update(value)

        def close_bar(self, key):
            with self.lock:
                if key in self.bars:
                    try:
                        self.bars[key].close()
                    except Exception:
                        pass
                    del self.bars[key]

        def close_all(self):
            with self.lock:
                for key, bar in list(self.bars.items()):
                    try:
                        bar.close()
                    except Exception:
                        pass
                self.bars.clear()

    progress_mgr = ProgressManager()

    async def download_file(url, save_path, desc, task_index, total_tasks, task_type, max_retries=3, retry_delay=5):
        from aiohttp import ClientSession, ClientPayloadError, ClientResponseError, ServerDisconnectedError
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        progress_bar_key = f"download_{task_index}_{task_type}"
        retry_count = 0

        while retry_count < max_retries:
            try:
                downloaded = 0
                if path.exists(save_path):
                    downloaded = path.getsize(save_path)
                    logger.info(f"发现已下载文件: {save_path}，大小: {downloaded} 字节")

                async with ClientSession() as session:
                    async with session.head(url, headers=headers) as response:
                        file_size = int(response.headers.get('content-length', 0))

                    if downloaded == file_size and file_size > 0:
                        logger.info(f"文件已完整下载，跳过: {save_path}")
                        return True

                    makedirs(path.dirname(save_path), exist_ok=True)

                    position = task_index % 10
                    progress_bar = progress_mgr.create_bar(
                        progress_bar_key, file_size,
                        f'[{task_index}/{total_tasks}] {desc} {task_type}',
                        position
                    )

                    if downloaded > 0:
                        progress_bar.update(downloaded)
                        headers['Range'] = f'bytes={downloaded}-'
                        logger.info(f"从断点 {downloaded}/{file_size} 字节继续下载...")
                    else:
                        if 'Range' in headers:
                            del headers['Range']

                    mode = 'ab' if downloaded > 0 else 'wb'
                    try:
                        async with session.get(url, headers=headers) as response:
                            if response.status != 200 and response.status != 206:
                                raise Exception(f"HTTP错误: {response.status}")
                            with open(save_path, mode) as f:
                                chunk_size = 32768
                                async for chunk in response.content.iter_chunked(chunk_size):
                                    if not chunk:
                                        break
                                    f.write(chunk)
                                    progress_mgr.update_bar(progress_bar_key, len(chunk))
                    except CancelledError:
                        logger.warning(f"下载任务被取消: {save_path}")
                        raise
                    except (ClientPayloadError, ServerDisconnectedError) as e:
                        logger.warning(f"数据接收不完整: {e}，将尝试恢复下载")
                    except Exception as e:
                        logger.error(f"下载过程中出错: {e}")
                        raise

                progress_mgr.close_bar(progress_bar_key)

                if not path.exists(save_path):
                    raise Exception(f"下载后文件不存在: {save_path}")

                actual_size = path.getsize(save_path)
                if file_size > 0 and actual_size != file_size:
                    logger.warning(f"文件大小不匹配，预期: {file_size}，实际: {actual_size}")
                    if actual_size < file_size * 0.98:
                        logger.info(f"删除不完整文件并重试: {save_path}")
                        try:
                            remove(save_path)
                        except Exception:
                            pass
                        raise Exception(f"下载不完整，需要重新下载")
                    else:
                        logger.info(f"接受文件，差异在可接受范围内")
                        return True
                else:
                    logger.info(f"完成下载: [{task_index}/{total_tasks}] {desc} {task_type}")
                    return True

            except (ClientResponseError, ServerDisconnectedError) as e:
                logger.error(f"网络错误: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"将在 {retry_delay} 秒后重试 ({retry_count}/{max_retries})")
                    await asyncio_sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"下载失败: [{task_index}] {desc} - 重试次数用尽")
                    raise
            except Exception as e:
                logger.error(f"下载失败: [{task_index}] {desc} - {e}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"将在 {retry_delay} 秒后重试 ({retry_count}/{max_retries})")
                    await asyncio_sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"下载失败: [{task_index}] {desc} - 重试次数用尽")
                    raise

        logger.error(f"下载失败: [{task_index}] {desc} - 重试{max_retries}次后失败")
        progress_mgr.close_bar(progress_bar_key)
        raise Exception(f"重试{max_retries}次后下载失败")

    async def login_with_qrcode():
        qr = QrCodeLogin(platform=QrCodeLoginChannel.WEB)
        await qr.generate_qrcode()
        print(qr.get_qrcode_terminal())
        print("请使用B站APP扫描以上二维码: （如果二维码面积过大无法显示，可以使用 Ctrl + 鼠标滚轮缩放）")

        while not qr.has_done():
            state = await qr.check_state()
            print(f"登录状态: {state}")
            await asyncio_sleep(1)

        print("登录成功")
        return qr.get_credential()

    def sanitize_filename(filename):
        sanitized = ILLEGAL_FILENAME_CHARS.sub('_', filename)
        sanitized = sanitized.strip()
        if not sanitized:
            sanitized = "未命名视频"
        return sanitized

    def build_ffmpeg_cmd(video_file, audio_file, output_file, use_gpu=False, width=1920, height=1080,
                         original_codec="h264", convert_to_h265=False, convert_framerate=False,
                         target_framerate=30, original_framerate=None, attempt=0):
        base_cmd = f'ffmpeg -y -i "{video_file}" -i "{audio_file}"'
        map_args = '-map 0:v:0 -map 1:a:0 -shortest'
        output_args = f'"{output_file}"'

        logger.info(f"帧率转换设置: convert_framerate={convert_framerate}, target_framerate={target_framerate}, original_framerate={original_framerate}")

        should_convert_framerate = False
        if convert_framerate and original_framerate is not None:
            if abs(target_framerate - original_framerate) > 0.5:
                should_convert_framerate = True
            else:
                logger.info(f"原始帧率{original_framerate}fps与目标帧率{target_framerate}fps相近，跳过转换")
        elif convert_framerate and original_framerate is None:
            should_convert_framerate = True
            logger.info("无法检测原始帧率，但用户要求帧率转换，强制执行")

        vf_filters = []
        if should_convert_framerate:
            vf_filters.append(f"fps={target_framerate}")
            logger.info(f"添加帧率转换滤镜: {original_framerate or '未知'}fps → {target_framerate}fps")
        if attempt >= 3:
            vf_filters.append(f"scale={width}:{height}")

        vf_args = f'-vf "{",".join(vf_filters)}"' if vf_filters else ""

        # 修复：编码器检测缓存，避免多集重复扫 ffmpeg
        if convert_to_h265:
            if use_gpu:
                gpu_encoders = ["hevc_nvenc", "h265_nvenc", "hevc_vaapi"]
                video_codec = None
                for encoder in gpu_encoders:
                    if check_encoder_supported(encoder):
                        video_codec = encoder
                        break
                if video_codec is None:
                    video_codec = "libx265"
                    logger.info("未找到可用的GPU编码器，回退到CPU编码")
            else:
                video_codec = "libx265"
        elif original_codec in ["h265", "hevc"]:
            video_codec = "copy"
        else:
            video_codec = "copy"

        cmd_options = [
            f'{base_cmd} -c:v copy -c:a copy {map_args} {output_args}',
            f'{base_cmd} {vf_args} -c:v {video_codec} -c:a copy {map_args} {output_args}',
            f'{base_cmd} {vf_args} -c:v {video_codec} -c:a copy {map_args} {output_args}' if not use_gpu else
            f'{base_cmd} {vf_args} -hwaccel cuda -c:v {video_codec} -c:a copy {map_args} {output_args}',
            f'{base_cmd} {vf_args} -c:v {video_codec} -c:a copy {map_args} {output_args}',
            f'{base_cmd} {vf_args} -c:v {video_codec} -preset fast -c:a copy {map_args} {output_args}',
            f'{base_cmd} {vf_args} -c:v libx265 -c:a copy {map_args} {output_args}'
        ]

        logger.info(f"方案 {attempt}: {cmd_options[attempt]}")
        if attempt < len(cmd_options):
            return cmd_options[attempt]
        return cmd_options[-1]

    _encoder_cache = {}
    def check_encoder_supported(encoder_name):
        if encoder_name in _encoder_cache:
            return _encoder_cache[encoder_name]
        try:
            result = run(['ffmpeg', '-encoders'], stdout=PIPE, stderr=PIPE, text=True, timeout=15)
            if result.returncode == 0:
                # 修复：按整词匹配，避免 hevc 误匹配 hevc_nvenc 等
                available = {line.split()[1] for line in result.stdout.splitlines() if line.strip().startswith(('V', 'A', 'S'))}
                ok = encoder_name in available
                if ok:
                    logger.info(f"编码器 {encoder_name} 可用")
                else:
                    logger.warning(f"编码器 {encoder_name} 不可用")
                _encoder_cache[encoder_name] = ok
                return ok
            else:
                logger.error(f"获取编码器列表失败: {result.stderr}")
                return False
        except TimeoutExpired:
            logger.error(f"检测编码器 {encoder_name} 超时")
            return False
        except Exception as e:
            logger.error(f"检测编码器 {encoder_name} 时出错: {e}")
            return False

    def ffmpeg_merge(video_file, audio_file, output_file, title, index, total_count, duration,
                     convert_to_h265=False, convert_framerate=False,
                     target_framerate=30, original_framerate=None, attempt=0):
        width = 1920
        height = 1080
        original_codec = "h264"

        try:
            try:
                cmd = [
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height,codec_name',
                    '-of', 'json',
                    video_file
                ]
                result = run(cmd, stdout=PIPE, stderr=PIPE, text=True, timeout=15)
                if result.returncode == 0:
                    video_info = loads(result.stdout)
                    for stream in video_info.get('streams', []):
                        if stream.get('codec_type') == 'video':
                            width = stream.get('width', 1920)
                            height = stream.get('height', 1080)
                            codec = stream.get('codec_name', 'h264')
                            if codec in ["h264", "avc"]:
                                original_codec = "h264"
                            elif codec in ["h265", "hevc"]:
                                original_codec = "h265"
                            else:
                                original_codec = codec
                            logger.info(f"检测到视频编码: {original_codec}, 分辨率: {width}x{height}")
                            break
                else:
                    logger.error(f"获取视频信息失败: {result.stderr}")
            except TimeoutExpired:
                logger.error("获取视频信息超时")
            except Exception as e:
                logger.error(f"无法获取视频信息: {e}, 将使用默认参数")

            start_attempt = 0
            if convert_to_h265 and original_codec in ["h264", "avc"]:
                logger.info("需要H265转换，跳过流复制方案")
                start_attempt = 1
            else:
                logger.info("不需要转换，使用流复制方案")

            position = index % 10
            progress_bar_key = f"ffmpeg_{index}"
            encode_progress_bar = progress_mgr.create_bar(
                progress_bar_key, duration,
                f'[{index}/{total_count}] {title} video [3/3]',
                position, unit='second'
            )

            success = False
            attempt = start_attempt
            max_attempts = 6

            while not success and attempt < max_attempts:
                try:
                    cmd_line = build_ffmpeg_cmd(
                        video_file, audio_file, output_file,
                        use_gpu=NVIDIA_GPU_SUPPORTED,
                        width=width, height=height,
                        original_codec=original_codec,
                        convert_to_h265=convert_to_h265,
                        convert_framerate=convert_framerate,
                        target_framerate=target_framerate,
                        original_framerate=original_framerate,
                        attempt=attempt
                    )

                    mode = "流复制" if attempt == 0 else ("GPU" if NVIDIA_GPU_SUPPORTED and attempt < 5 else "CPU")
                    conversion = ""
                    if convert_to_h265 and original_codec in ["h264", "avc"]:
                        conversion = " (H265转换)"
                    logger.info(f"尝试{mode}方案 {attempt+1}/{max_attempts} 合成视频 [{index}/{total_count}]{conversion}")

                    if attempt > start_attempt:
                        encode_progress_bar.reset()

                    process = Popen(
                        cmd_line, stdout=PIPE, stderr=STDOUT,
                        universal_newlines=True, encoding='utf-8',
                        shell=True, bufsize=1
                    )

                    for line in process.stdout:
                        if line.startswith('size='):
                            time_length = parse_time_2_sec(line)
                            progress_mgr.update_bar(progress_bar_key, time_length - encode_progress_bar.n)

                    return_code = process.wait()
                    if return_code == 0:
                        success = True
                        logger.info(f"{mode}方案 {attempt+1} 成功!")

                        actual_codec = detect_video_codec(output_file)
                        if actual_codec:
                            logger.info(f"输出视频编码: {actual_codec}")
                            if convert_to_h265 and original_codec in ["h264", "avc"]:
                                if "hevc" in actual_codec.lower() or "h265" in actual_codec.lower():
                                    logger.info("H265转换成功")
                                else:
                                    logger.warning("H265转换失败！视频未转换为H265编码")
                                    success = False
                                    attempt += 1
                                    continue

                        actual_framerate = detect_video_framerate(output_file)
                        if actual_framerate:
                            logger.info(f"输出视频帧率: {actual_framerate}fps")
                            if convert_framerate:
                                framerate_diff = abs(actual_framerate - target_framerate)
                                if framerate_diff < 0.5:
                                    logger.info("帧率转换成功")
                                else:
                                    logger.warning(f"帧率转换未达到预期! 目标: {target_framerate}fps, 实际: {actual_framerate}fps")
                                    if original_framerate:
                                        logger.info(f"原始帧率: {original_framerate}fps, 目标: {original_framerate}fps")
                        else:
                            logger.warning("无法检测输出视频帧率")
                    else:
                        logger.warning(f"{mode}方案 {attempt+1} 失败，返回码: {return_code}")
                        attempt += 1
                except Exception as e:
                    logger.error(f"{mode}方案 {attempt+1} 异常: {e}")
                    attempt += 1

            progress_mgr.close_bar(progress_bar_key)

            if not success:
                raise Exception("所有编码方式都失败了，无法合成视频")
            if not path.exists(output_file):
                raise Exception(f"输出文件不存在: {output_file}")
            if path.getsize(output_file) == 0:
                raise Exception(f"输出文件大小为0: {output_file}")

            logger.info(f"视频 [{index}/{total_count}] '{title}' 合成成功: {output_file}")

            try:
                if path.exists(audio_file):
                    remove(audio_file)
                if path.exists(video_file):
                    remove(video_file)
            except Exception as e:
                logger.warning(f"清理临时文件时出错，但不影响结果: {e}")

            return True
        except Exception as e:
            logger.error(f"合成视频 {index} 时出错: {e}")
            try:
                progress_mgr.close_bar(progress_bar_key)
            except Exception:
                pass
            return False

    def cleanup_temp_dir():
        temp_dir = './download/temp'
        if path.exists(temp_dir):
            try:
                for filename in listdir(temp_dir):
                    file_path = path.join(temp_dir, filename)
                    try:
                        if path.isfile(file_path):
                            remove(file_path)
                        elif path.isdir(file_path):
                            shutil.rmtree(file_path)
                    except Exception as e:
                        logger.warning(f"无法删除临时文件 {file_path}: {e}")
            except Exception as e:
                logger.error(f"清理临时目录时出错: {e}")

    async def process_episode(ep, position_index, total_count, semaphore, course_folder,
                              original_index, convert_to_h265=False, convert_framerate=False,
                              target_framerate=30, max_retries=3, retry_delay=5):
        # 修复：临时文件路径提前定义，确保异常清理一定能删到
        filename_prefix = f"{original_index}_{uuid4().hex}"
        audio_file = f"./download/temp/{filename_prefix}_audio.m4s"
        video_file = f"./download/temp/{filename_prefix}_video.m4s"
        safe_title = "episode"
        output_file = None
        try:
            async with semaphore:
                ep_id = ep.get_epid()
                meta = await ep.get_meta()
                original_title = meta['title']
                safe_title = sanitize_filename(original_title)
                title = format_title(safe_title)

                output_file = f"./download/{course_folder}/{original_index:03d}_{safe_title}.mp4"
                makedirs(f"./download/{course_folder}", exist_ok=True)

                download_url_data = await ep.get_download_url()
                detector = video.VideoDownloadURLDataDetecter(data=download_url_data)
                try:
                    streams = detector.detect_best_streams()
                except (ValueError, KeyError) as e:
                    # 修复 C：B 站 API 返回了库内 AudioQuality 枚举未收录的新音质 ID
                    # （如 100010 Hi-Res），detector 内部 AudioQuality(id) 会抛 ValueError。
                    # 此时退化为手动从 download_url_data 取第一个可用流，保证不中断。
                    logger.warning(f"detect_best_streams 失败({e})，回退手动选流")
                    dash = download_url_data.get("dash") or download_url_data.get("data", {}).get("dash")
                    if not dash:
                        raise Exception(f"无法解析下载地址，返回数据: {str(download_url_data)[:200]}")
                    video_streams = dash.get("video", [])
                    audio_streams = dash.get("audio", [])
                    if not video_streams or not audio_streams:
                        raise Exception("音视频流列表为空")
                    # 按清晰度排序，取最优
                    video_streams_sorted = sorted(video_streams, key=lambda s: s.get("id", 0), reverse=True)
                    audio_streams_sorted = sorted(audio_streams, key=lambda s: s.get("id", 0), reverse=True)
                    # 用 SimpleNamespace 模拟流对象，保持 streams[0]/streams[1] 用法兼容
                    from types import SimpleNamespace
                    streams = [
                        SimpleNamespace(url=video_streams_sorted[0]["baseUrl"] or video_streams_sorted[0].get("base_url", "")),
                        SimpleNamespace(url=audio_streams_sorted[0]["baseUrl"] or audio_streams_sorted[0].get("base_url", "")),
                    ]

                await download_file(streams[1].url, audio_file, title, position_index, total_count, "audio [1/3]", max_retries, retry_delay)
                await download_file(streams[0].url, video_file, title, position_index, total_count, "video [2/3]", max_retries, retry_delay)

                if not path.exists(audio_file) or path.getsize(audio_file) == 0:
                    raise Exception(f"音频文件下载失败或大小为0: {audio_file}")
                if not path.exists(video_file) or path.getsize(video_file) == 0:
                    raise Exception(f"视频文件下载失败或大小为0: {video_file}")

                video_meta = await ep.get_meta()
                duration = video_meta.get('duration', 0)

                original_framerate = None
                if convert_framerate:
                    try:
                        original_framerate = detect_video_framerate(video_file)
                        if original_framerate:
                            logger.info(f"检测到视频原始帧率: {original_framerate}fps")
                        else:
                            logger.warning("无法检测视频帧率，使用默认值60fps")
                            original_framerate = 60.0
                    except Exception as e:
                        logger.error(f"检测视频帧率失败: {e}")
                        original_framerate = 60.0

                result = ffmpeg_merge(
                    video_file, audio_file, output_file, title,
                    position_index, total_count, duration,
                    convert_to_h265, convert_framerate, target_framerate, original_framerate
                )

                return {"success": result, "position_index": position_index, "original_index": original_index, "episode": ep}
        except Exception as e:
            logger.error(f"处理视频 {position_index} 时出错: {e}")
            # 修复：直接删除已定义的临时文件，不依赖 locals()
            for tmp in (audio_file, video_file):
                try:
                    if path.exists(tmp):
                        remove(tmp)
                except Exception:
                    pass
            error_info = {
                "position_index": position_index,
                "original_index": original_index,
                "episode_id": ep.get_epid() if ep else None,
                "error": str(e)
            }
            try:
                from json import dump as _json_dump
                with open(f"./download/failed/{original_index:03d}_error.json", "w", encoding="utf-8") as f:
                    # 修复 B：写文件必须用 json.dump()，dumps() 不接受文件句柄作位置参数
                    _json_dump(error_info, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            return {"success": False, "position_index": position_index, "original_index": original_index, "episode": ep, "error": str(e)}

    # 修复：input 安全包装，避免 EOFError 闪退
    def safe_input(prompt=""):
        try:
            return input(prompt)
        except EOFError:
            print("\n[提示] 未检测到输入（可能是在非交互环境运行）。请直接双击或在终端中运行本程序。")
            raise
        except KeyboardInterrupt:
            raise

    async def main():
        ensure_dirs()
        cleanup_temp_dir()

        if not check_ffmpeg():
            print("\n程序无法继续，请安装FFmpeg后重试。")
            return

        config = load_config()
        default_convert_to_h265 = config.getboolean('General', 'convert_to_h265', fallback=False)
        default_concurrent_downloads = config.getint('General', 'concurrent_downloads', fallback=2)
        default_concurrent_ffmpeg = config.getint('General', 'concurrent_ffmpeg', fallback=1)
        gpu_mode = config.get('General', 'gpu_mode', fallback='auto')
        max_retries = config.getint('General', 'max_retries', fallback=3)
        retry_delay = config.getint('General', 'retry_delay', fallback=5)

        default_convert_framerate = config.getboolean('General', 'convert_framerate', fallback=False)
        default_target_framerate = config.getint('General', 'target_framerate', fallback=30)

        print("\n== 帧率转换设置 ==")
        print("开启帧率转换可以调整视频的流畅度")
        print("常见帧率: 24 (电影感), 30 (标准), 60 (流畅)")
        choice = safe_input(f"请选择 1=开启 / 2=保持原样 (默认: {'开启' if default_convert_framerate else '保持'}): ").strip() or None

        convert_framerate = default_convert_framerate
        target_framerate = default_target_framerate

        if choice == '1':
            convert_framerate = True
            try:
                user_input = safe_input(f"请输入目标帧率 (默认: {default_target_framerate}): ").strip()
                if user_input:
                    target_framerate = int(user_input)
                    if target_framerate < 1 or target_framerate > 120:
                        logger.warning(f"无效的帧率 {target_framerate}，使用默认值 {default_target_framerate}")
                        target_framerate = default_target_framerate
            except ValueError:
                logger.warning(f"输入无效，使用默认帧率 {default_target_framerate}")
                target_framerate = default_target_framerate

        print("\n== H265转换设置 ==")
        print("开启H265转换可以减小文件大小（约30-50%），但会增加处理时间")
        print("注意：只有原始编码为H264的视频会被转换")
        choice = safe_input(f"请选择 1=开启H265 / 2=保持原始 (默认: {'开启' if default_convert_to_h265 else '保持'}): ").strip() or None

        convert_to_h265 = default_convert_to_h265
        if choice == '1':
            convert_to_h265 = True
        elif choice == '2':
            convert_to_h265 = False

        if convert_to_h265:
            logger.info("已启用H265转换")
            hevc_supported = check_h265_support(NVIDIA_GPU_SUPPORTED)
            if not hevc_supported:
                logger.warning("当前系统不支持H265编码，将使用H264")
                convert_to_h265 = False
            else:
                logger.info("系统支持H265编码")

        print("\n== 硬件加速设置 ==")
        print("1. 自动检测 (默认)")
        print("2. 强制使用GPU")
        print("3. 强制使用CPU")
        print(f"当前配置: {gpu_mode}")
        choice = safe_input("请选择 (输入数字1-3，直接回车使用配置文件设置): ").strip()

        force_mode = None
        if choice == '1' or (not choice and gpu_mode == 'auto'):
            force_mode = None
        elif choice == '2' or (not choice and gpu_mode == 'force_gpu'):
            force_mode = True
        elif choice == '3' or (not choice and gpu_mode == 'force_cpu'):
            force_mode = False

        check_nvidia_gpu_support(force_mode)

        credential = None
        if path.exists('./bilibili.session'):
            try:
                with open('bilibili.session', 'r', encoding='utf-8') as file:
                    cookies_data = loads(file.read())
                    credential = Credential(
                        sessdata=cookies_data.get('SESSDATA', ''),
                        bili_jct=cookies_data.get('bili_jct', ''),
                        buvid3=cookies_data.get('buvid3', '')
                    )
                if not await credential.check_valid():
                    logger.info("凭证已过期，需要重新登录")
                    credential = await login_with_qrcode()
            except Exception as e:
                logger.error(f"读取会话文件出错: {e}")
                credential = await login_with_qrcode()
        else:
            credential = await login_with_qrcode()

        with open('bilibili.session', 'w', encoding='utf-8') as file:
            file.write(dumps(credential.get_cookies(), indent=4, ensure_ascii=False))

        print('请输入要下载的课程序号,只需要最后的ID')
        print('例如你的课程地址是https://www.bilibili.com/cheese/play/ss360')
        print('那么你的课程ID是 ss360 ')
        input_id = safe_input('请输入要下载的课程序号: ')

        try:
            if input_id.startswith('ss'):
                season_id = int(input_id[2:])
                cheese_list = cheese.CheeseList(season_id=season_id, credential=credential)
            else:
                try:
                    season_id = int(input_id)
                    cheese_list = cheese.CheeseList(season_id=season_id, credential=credential)
                except ValueError:
                    print("无效的课程ID，请确保输入正确的格式")
                    return

            try:
                course_info = await cheese_list.get_meta()
                if 'title' not in course_info:
                    print(f"获取课程信息失败，API返回: {course_info}")
                    return

                course_title = sanitize_filename(course_info['title'])
                print(f"正在下载课程: {course_title}")

                course_folder = course_title
                if not path.exists(f"./download/{course_folder}"):
                    makedirs(f"./download/{course_folder}")

                episodes = await cheese_list.get_list()

                print("\n课程包含以下集数:")
                for i, ep in enumerate(episodes, 1):
                    meta = await ep.get_meta()
                    title = meta.get('title', f'第{i}集')
                    print(f"[{i}] {title}")

                print("\n请选择要下载的集数:")
                print("1. 全部下载")
                print("2. 下载指定集数 (逗号分隔, 如 1,3,5)")
                print("3. 下载范围集数")
                choice = safe_input("请输入选项 (1-3): ").strip()

                selected_episodes = []

                if choice == '1':
                    selected_episodes = [(i, ep) for i, ep in enumerate(episodes, 1)]
                elif choice == '2':
                    episodes_input = safe_input("请输入要下载的集数 (用逗号分隔, 如: 1,3,5): ").strip()
                    if episodes_input:
                        try:
                            episode_nums = [int(num.strip()) for num in episodes_input.split(',')]
                            for num in episode_nums:
                                if 1 <= num <= len(episodes):
                                    selected_episodes.append((num, episodes[num-1]))
                                else:
                                    print(f"忽略无效集数: {num}")
                        except ValueError:
                            print("输入格式错误，请使用数字和逗号分隔")
                elif choice == '3':
                    start_input = safe_input("请输入起始集数: ").strip()
                    end_input = safe_input("请输入结束集数: ").strip()
                    try:
                        start = int(start_input)
                        end = int(end_input)
                        if 1 <= start <= len(episodes) and 1 <= end <= len(episodes) and start <= end:
                            for num in range(start, end + 1):
                                selected_episodes.append((num, episodes[num-1]))
                        else:
                            print("无效的范围，将下载全部")
                            selected_episodes = [(i, ep) for i, ep in enumerate(episodes, 1)]
                    except ValueError:
                        print("输入格式错误，将下载全部")
                        selected_episodes = [(i, ep) for i, ep in enumerate(episodes, 1)]
                else:
                    print("无效选择，将下载全部")
                    selected_episodes = [(i, ep) for i, ep in enumerate(episodes, 1)]

                if not selected_episodes:
                    print("没有选择任何集数，程序退出")
                    return

                print(f"已选择下载 {len(selected_episodes)} 集视频")

                concurrent_downloads = default_concurrent_downloads
                try:
                    user_input = safe_input(f'请输入并行下载的数量（默认为{default_concurrent_downloads}）: ').strip()
                    if user_input:
                        concurrent_downloads = int(user_input)
                        if concurrent_downloads < 1:
                            concurrent_downloads = 1
                        elif concurrent_downloads > len(selected_episodes):
                            concurrent_downloads = len(selected_episodes)
                except ValueError:
                    logger.warning(f"输入无效，使用默认值{default_concurrent_downloads}")
                    concurrent_downloads = default_concurrent_downloads

                semaphore = Semaphore(concurrent_downloads)
                tasks = []
                for i, (original_index, ep) in enumerate(selected_episodes, 1):
                    task = create_task(process_episode(
                        ep, i, len(selected_episodes), semaphore, course_folder,
                        original_index, convert_to_h265, convert_framerate,
                        target_framerate, max_retries, retry_delay
                    ))
                    tasks.append(task)

                results = await gather(*tasks)

                success_count = sum(1 for r in results if r.get("success", False))
                failed_count = len(selected_episodes) - success_count
                print(f"\n下载完成，成功: {success_count}, 失败: {failed_count}")

                if failed_count > 0:
                    print("\n失败的任务:")
                    for result in results:
                        if not result.get("success", False):
                            try:
                                meta = await result['episode'].get_meta()
                                title = meta.get('title', f'第{result["original_index"]}集')
                            except Exception:
                                title = f'第{result["original_index"]}集'
                            print(f"  - [{result['original_index']}] {title}: {result.get('error', '未知错误')}")
                            print(f"    错误详情已保存到: ./download/failed/{result['original_index']:03d}_error.json")

            except Exception as e:
                logger.error(f"处理课程信息时出错: {e}")
                traceback.print_exc()

        except Exception as e:
            logger.error(f"处理课程ID时出错: {e}")
            traceback.print_exc()

        progress_mgr.close_all()
        cleanup_temp_dir()

    return asyncio_run(main())


# ============ 启动入口 ============
def _pause_before_exit():
    """让错误信息能在屏幕上停留，避免双击运行一闪而过"""
    if sys.stdout.isatty():
        # 终端里运行，不强制暂停（让用户自己 control）
        return
    try:
        input("\n按回车键退出...")
    except Exception:
        pass


def main_entry():
    print("=" * 60)
    print("  bilibili-cheese-downloader 3.0 (闪退修复版)")
    print("=" * 60)

    # 0. Python 版本
    if not _check_python():
        _pause_before_exit()
        sys.exit(1)

    # 1. 依赖
    ok, missing = _check_dependencies()
    if not ok:
        _pause_before_exit()
        sys.exit(1)

    # 2. ffmpeg
    if not _check_ffmpeg():
        _pause_before_exit()
        sys.exit(1)

    # 3. bilibili_api 内部接口兼容
    ctx = _patch_bilibili_api_imports()
    if ctx is None:
        _pause_before_exit()
        sys.exit(1)

    print("[自检通过] Python / 依赖 / ffmpeg / bilibili_api 接口均正常，开始运行...\n")

    # 4. 真正运行
    _run_real_main(ctx)


if __name__ == "__main__":
    try:
        main_entry()
    except KeyboardInterrupt:
        print("\n[用户中断] 程序已停止")
    except EOFError:
        print("\n[提示] 未检测到输入，可能是在非交互环境运行。")
        print("       请在终端 / 命令行中运行本程序。")
    except Exception as e:
        print("\n" + "=" * 60)
        print("程序异常退出，错误信息：")
        traceback.print_exc()
        print("详细日志已写入 bdownloader.log")
        print("=" * 60)
    finally:
        _pause_before_exit()
