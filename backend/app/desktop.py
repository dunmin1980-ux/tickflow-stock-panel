"""桌面客户端入口 — uvicorn 后台服务 + pywebview 桌面窗口。

运行方式:
  开发模式: python -m app.desktop  (需 pip install pywebview)
  打包后:   双击可执行文件即可

职责:
  1. 单实例锁 — 已运行则聚焦已有窗口并退出
  2. 选可用端口 — 从 settings.port 起, 被占则递增
  3. 后台线程起 uvicorn (仅监听 127.0.0.1, 不暴露外网)
  4. 主线程起 pywebview 窗口渲染前端
  5. 窗口关闭 → 优雅停止 uvicorn → 进程退出

不含: 业务逻辑、配置持久化、监控告警 (全在 app.main 里)。
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from contextlib import suppress

from app.desktop_runtime import DesktopServer

logger = logging.getLogger(__name__)

_APP_NAME = "TickFlow 股票面板"


def _ensure_data_dir_writable() -> None:
    """确保用户数据目录可写 (lifespan 会创建子目录, 这里只验证根目录)。

    data_dir 在 frozen 模式下指向用户目录 (见 config.py), 非可写会导致
    DuckDB 视图 / parquet 落盘全失败。提前失败胜过启动后乱报错。
    """
    from app.config import settings

    data_root = settings.data_dir
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as e:
        logger.error("数据目录不可写, 桌面版无法运行: %s (%s)", data_root, e)
        raise


def _acquire_single_instance() -> bool:
    """单实例锁。已运行返回 False (本进程应退出), 否则 True。

    用 data_dir/.desktop.lock 文件锁实现。跨进程, 文件存在即视为已运行
    (简单可靠; 不引入 msvcrt/fcntl 平台差异)。
    """
    from app.config import settings

    lock_path = settings.data_dir / ".desktop.lock"
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                pid_str = lock_path.read_text(encoding="utf-8").strip()
                pid = int(pid_str) if pid_str.isdigit() else None
            except Exception:
                pid = None

            if pid is not None and _pid_alive(pid):
                logger.warning("检测到已有实例运行 (PID %d), 本进程退出", pid)
                return False
            logger.info("清理残留单实例锁 (PID %s 已不存在)", pid)
            with suppress(FileNotFoundError):
                lock_path.unlink()
            continue

        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(str(_current_pid()))
        return True


def _release_single_instance() -> None:
    from app.config import settings

    lock_path = settings.data_dir / ".desktop.lock"
    try:
        owner = lock_path.read_text(encoding="utf-8").strip()
        if owner == str(_current_pid()):
            lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _guard_streams() -> None:
    """windowed 模式 (console=False) 下守护 stdout/stderr。

    PyInstaller console=False 用 runw.exe 启动器, 不分配控制台, 此时 sys.stdout /
    sys.stderr 可能为 None (或底层句柄无效)。后果:
      - app/__init__.py 早期 reconfigure 遇 None 虽有 hasattr 保护, 但若是个「写入即崩」
        的伪 stream 对象, reconfigure 会成功、后续写却崩;
      - logging.basicConfig() 默认建 StreamHandler(sys.stderr), stderr 为 None 时
        首次写日志调 None.write() 抛 AttributeError, 此时往往在导入早期,
        Python 异常处理未就绪 → 进程直接闪退, try/except 都拦不住。

    修法: console=False 下把 stdout/stderr 换成丢弃写入的空对象 (devnull),
    让 logging / reconfigure / 任何 print 都安全落地。console=True 不动 (有真控制台)。
    """
    class _NullStream:
        """丢弃所有写入的空流 (替代 None 的 stdout/stderr)。"""
        def write(self, _s): return 0
        def flush(self): pass
        def reconfigure(self, *a, **kw): pass
        def isatty(self): return False
        def fileno(self): raise OSError("no fileno")

    # 仅在 stdout/stderr 缺失或不可写时替换 (有真控制台时保持原样)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _NullStream())


def _setup_logging() -> None:
    """配置日志落盘到 data/desktop.log。

    背景: spec 里 console=False (桌面应用不弹黑窗), 导致 logging 默认输出的
    stderr 被吞掉 —— 启动期任何异常用户都看不到, 表现为「双击一闪退出、查无日志」。
    这里追加一个 FileHandler, 让日志同时落到 data_dir/desktop.log, 事后可查。

    时序注意: data_dir 在 import app.config 时路径已可用, 但目录此刻可能不存在
    (frozen 首次运行), 必须先 mkdir, 否则 FileHandler 打开文件会抛 FileNotFoundError。
    不用第二次 basicConfig (它「首次调用才生效」), 改用 addHandler 追加。
    """
    try:
        from app.config import settings

        log_dir = settings.data_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            log_dir / "desktop.log",
            mode="a",            # 追加, 保留历史 (排查时往往需要对比多次启动)
            encoding="utf-8",
            errors="replace",    # 容错: 对齐 __init__.py 的 stderr 重配, 避免中文/emoji 触发 UnicodeEncodeError
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
    except Exception as e:
        # 日志落盘失败不阻断启动 (开发模式 data_dir 可能不可写)
        logger.warning("日志文件初始化失败, 仅输出到 stderr: %s", e)


def _show_crash(title: str, text: str) -> None:
    """崩溃时弹原生 MessageBox 提示用户 (仅 Windows)。

    console=False 下用户看不到任何输出, 崩溃时弹一个原生错误框, 让用户至少
    知道「程序崩了 + 原因」, 并可截图反馈。非 Windows 用日志降级, 不调 ctypes。

    ctypes 是 Python 标准库, 不引入新依赖; MessageBoxW 是 Unicode 版本 (W 后缀),
    支持中文标题/正文。0x10 = MB_ICONERROR (红色错误图标)。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        except Exception as e:
            logger.error("弹框失败 (已写日志文件): %s", e)
    else:
        logger.error("%s: %s", title, text)


def _pid_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否存活。"""
    import os

    if os.name == "nt":
        # Windows: 0 表示存在, 其它是异常
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    else:
        try:
            os.kill(pid, 0)  # signal 0 = 探测存活, 不实际发信号
            return True
        except OSError:
            return False


def _current_pid() -> int:
    import os

    return os.getpid()


def _open_window(url: str) -> None:
    """主线程: 用 pywebview 打开桌面窗口。"""
    import webview  # type: ignore[import-not-found]

    webview.create_window(
        _APP_NAME,
        url,
        width=1440,
        height=900,
        min_size=(1024, 700),
        # 桌面版固定单窗口, 禁用外部浏览器跳转
        confirm_close=False,
    )
    # pywebview 会阻塞主线程直到窗口关闭
    webview.start(debug=False)


def main() -> int:
    """桌面客户端主入口。返回进程退出码。"""
    # 必须最先执行: console=False 下 stdout/stderr 可能无效, 不守护会导致
    # 后续 logging.basicConfig 创建的 StreamHandler 写日志时进程崩溃。
    _guard_streams()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 追加文件日志: console=False 下 stderr 被吞, 必须落盘否则查无对证。
    # 放在 basicConfig 之后 (它先建好 root logger 的格式), 这里只追加 handler。
    _setup_logging()

    try:
        _ensure_data_dir_writable()
    except Exception:
        # 数据目录不可写是致命错误, 无法继续
        return 1

    # 单实例: 已运行则退出
    if not _acquire_single_instance():
        return 0

    server: DesktopServer | None = None
    server_started = False
    try:
        os.environ.setdefault("TICKFLOW_DESKTOP_CLIENT", "1")
        server = DesktopServer(port=0)
        server.start()
        server_started = True

        if not server.wait_ready(60):
            logger.error("后端启动超时, 桌面版退出")
            return 1

        logger.info("桌面版后端监听 127.0.0.1:%d", server.bound_port)
        if os.getenv("TICKFLOW_DESKTOP_SMOKE") == "1":
            return 0

        url = f"http://127.0.0.1:{server.bound_port}"
        logger.info("打开桌面窗口: %s", url)
        _open_window(url)

        # 窗口关闭后, 进程退出 (daemon 线程会被回收)
        logger.info("窗口已关闭, 桌面版退出")
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # 顶层兜底: console=False 下未捕获异常会「一闪退出」且无任何反馈。
        # 写完整 traceback 到 data/desktop.log, 并弹原生 MessageBox 让用户截图反馈。
        # 必须排在 KeyboardInterrupt 之后 —— Exception 是基类, 在前会遮蔽它。
        logger.exception("桌面客户端启动失败")
        _show_crash("TickFlow 启动失败", traceback.format_exc())
        return 1
    finally:
        if server_started and server is not None:
            try:
                server.stop(10)
            except Exception:
                logger.exception("桌面版后端停止失败")
        _release_single_instance()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
