# capture 模块公开接口

from .screen import capture_virtual_desktop, virtual_geometry
from .overlay import ScreenShotWindow
from .flow import ScreenshotFlow
from .longshot import LongShotFlow
from .snap import RegionDetector

__all__ = ["capture_virtual_desktop", "virtual_geometry",
           "ScreenShotWindow", "ScreenshotFlow", "LongShotFlow",
           "RegionDetector"]
