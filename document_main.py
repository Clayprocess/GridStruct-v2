# 识别文档独立入口（GridStruct-V2 识别文档.exe）：不进托盘，直接选文件识别表格。
# 用法：python document_main.py [图片或 PDF ...]；把文件拖到 EXE 上也会直接打开。
# 与托盘主程序同包、共用 _internal 和 user-data，可以同时运行；所有识别窗口关掉即退出。

import sys

if __name__ == "__main__":
    from dpi import initialize_dpi_awareness
    initialize_dpi_awareness()

from PySide6.QtWidgets import QApplication

from settings import get_settings
from styles import apply_theme
from ui import app_icon, show_message
from ui.motion import enable_menu_effects


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GridStruct-V2 识别文档")
    app.setWindowIcon(app_icon())
    try:
        config = get_settings()
    except OSError as exc:
        show_message(None, "无法读取便携配置", f"{exc}\n请将应用放在有写入权限的目录。")
        return 1
    app.setWindowIcon(app_icon(apply_theme(app, config.value("theme", "system"))))
    enable_menu_effects(app)
    from table import open_document
    if open_document(paths=sys.argv[1:]) is None:
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
