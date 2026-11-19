import sys
from unittest.mock import Mock
from uuid import uuid4

import pytest
from PySide6.QtCore import QProcess, QSharedMemory

import main


# 用旧版的创建方式持锁，模拟用户已经运行的托盘。测试使用独立键。
OWNER = """
import sys
from PySide6.QtCore import QSharedMemory

lock = QSharedMemory(sys.argv[1])
assert lock.create(1), lock.errorString()
print("ready", flush=True)
sys.stdin.buffer.read(1)
assert lock.detach(), lock.errorString()
"""


@pytest.mark.parametrize("kill_owner", [
    pytest.param(False, id="normal-exit"),
    pytest.param(True, id="forced-exit", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="进程终止后的映射句柄回收由 Windows 提供",
    )),
])
def test_second_process_can_start_only_after_owner_exits(kill_owner):
    key = f"GridStructTest_{uuid4().hex}"
    owner = QProcess()
    owner.start(sys.executable, ["-c", OWNER, key])
    try:
        assert owner.waitForReadyRead(15000), bytes(owner.readAllStandardError())
        assert bytes(owner.readAllStandardOutput()).strip() == b"ready"
        assert main._ensure_single_instance(key) is None
        if kill_owner:
            owner.kill()
        else:
            owner.closeWriteChannel()
        assert owner.waitForFinished(15000)
        if not kill_owner:
            assert owner.exitCode() == 0, bytes(owner.readAllStandardError())
        successor = main._ensure_single_instance(key)
        assert successor is not None
        assert successor.detach()
    finally:
        if owner.state() != QProcess.ProcessState.NotRunning:
            owner.kill()
            owner.waitForFinished(15000)


def test_lock_creation_failure_is_visible_and_exits_with_error(monkeypatch):
    memory = Mock(spec=QSharedMemory)
    memory.create.return_value = False
    memory.error.return_value = QSharedMemory.SharedMemoryError.PermissionDenied
    memory.errorString.return_value = "Permission denied"
    shared_memory = Mock(return_value=memory, SharedMemoryError=QSharedMemory.SharedMemoryError)
    message = Mock()
    monkeypatch.setattr(main, "QSharedMemory", shared_memory)
    monkeypatch.setattr(main, "QApplication", Mock())
    monkeypatch.setattr(main, "show_message", message)

    assert main.main() == 1
    shared_memory.assert_called_once_with("PixShot_SingleInstance_v1")
    message.assert_called_once_with(None, "无法检查应用是否已启动", "Permission denied")
