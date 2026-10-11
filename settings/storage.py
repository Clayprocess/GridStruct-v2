"""便携配置随应用目录保存；首次启动保留并迁移旧注册表配置。"""

import sys
from pathlib import Path

from PySide6.QtCore import QSettings


def data_directory() -> Path:
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    path = root / "user-data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_settings() -> QSettings:
    path = data_directory() / "settings.ini"
    first_run = not path.exists()
    settings = QSettings(str(path), QSettings.IniFormat)
    if settings.status() != QSettings.NoError:
        raise OSError(f"便携配置无法读取或格式损坏：{path}")
    if first_run:
        legacy = QSettings("GridStruct", "GridStruct")
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
        settings.setValue("config_version", 1)
        settings.sync()
        if settings.status() != QSettings.NoError:
            raise OSError(f"无法写入便携配置：{path}")
    return settings
