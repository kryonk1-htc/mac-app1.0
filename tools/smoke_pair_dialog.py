"""配对窗口冒烟检查：点「连接账号」会不会崩。

来历：`pair_dialog.py` 里 `.format()` 一度被写在 `setStyleSheet()` 的返回值上
（`setStyleSheet()` 返回 None），点「连接账号」直接 AttributeError 闪退。
单元自测不碰 UI，抓不到这类"构造即崩"，所以单独留这个脚本。

只走 UI 路径（不联网、不动真库，DB 指到临时目录）：
    Widget → PairDialog(widget) → set_code → exec_()

跑法（会真实闪一次窗口，约 1 秒）：
    python tools/smoke_pair_dialog.py
失败时退出码非 0，faulthandler 会把 C 层栈打到 stderr。
"""

from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
from pathlib import Path

faulthandler.enable()

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

_TMP = Path(tempfile.gettempdir()) / "orbyte-repro"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ["ORBYTE_DB_PATH"] = str(_TMP / "repro.db")

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from orbyte.db import database  # noqa: E402
from orbyte.ui import theme  # noqa: E402
from orbyte.ui.pair_dialog import PairDialog  # noqa: E402
from orbyte.ui.widget import Widget  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    database.init_schema()
    theme.apply("light")

    widget = Widget()
    widget.show()
    print("widget shown", flush=True)

    dialog = PairDialog(widget)
    dialog.openRequested.connect(lambda url: print("openRequested:", url, flush=True))

    def finish() -> None:
        print("dialog visible =", dialog.isVisible(), flush=True)
        dialog.set_code("123456", "https://example.com/x")
        print("set_code ok", flush=True)
        dialog.accept()

    QTimer.singleShot(600, finish)
    print("before exec_", flush=True)
    dialog.exec_()
    print("exec_ returned", flush=True)

    widget.hide()
    app.quit()
    print("REPRO_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
