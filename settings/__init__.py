# settings 模块公开接口

from .storage import data_directory, get_settings
from .dialog import SettingsDialog, check_updates, _VERSION as VERSION

__all__ = ["SettingsDialog", "VERSION", "check_updates", "data_directory", "get_settings"]
