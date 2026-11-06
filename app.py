"""启动入口（源码运行与打包都用它）。

为什么单独一个文件：`python -m orbyte` 走的是包内相对导入，
PyInstaller 把入口脚本当 `__main__` 执行，相对导入会失败。
所以统一从这里的绝对导入进包。
"""

from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

# 源码运行时把 src 放进搜索路径；打包后模块已在归档里，这句无副作用
_src = Path(__file__).resolve().parent / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from orbyte.paths import DATA_DIR, ensure_dirs  # noqa: E402

#: 必须留一个模块级引用：faulthandler 只持有文件描述符，文件对象被回收就写不进去了
_crash_file = None


def _enable_crash_log() -> None:
    """把原生崩溃的线程栈落到 %LOCALAPPDATA%\\ORBYTE\\crash.log。

    原生崩溃（例如 Qt 里的 0xc0000005）既不触发 sys.excepthook 也不写我们
    自己的日志，进程就是"毫无征兆消失"。faulthandler 是这类问题唯一能留下的
    现场，所以启动就开，平时不产生任何内容。
    """
    global _crash_file
    try:
        ensure_dirs()
        _crash_file = open(DATA_DIR / "crash.log", "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        pass  # 开不了也照常启动，诊断能力缺失不该拦住产品


_enable_crash_log()

from orbyte.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
