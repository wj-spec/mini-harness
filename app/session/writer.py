"""会话执行期间的单写者锁；崩溃由操作系统释放。"""
from __future__ import annotations

import os
import stat
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None


class SessionBusy(OSError):
    pass


class SessionWriter:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.fd: int | None = None

    def __enter__(self):
        if fcntl is None:
            raise OSError('当前平台不支持会话执行锁')
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open('writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError('会话执行锁不是普通文件')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise SessionBusy('此会话正在其他连接运行；请从任务入口接入，或等待结束后重试。') from exc
                linked = os.stat('writer.lock', dir_fd=directory, follow_symlinks=False)
                if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
                    raise OSError('会话执行锁已被替换')
                self.fd = fd
            except BaseException:
                os.close(fd)
                raise
        finally:
            os.close(directory)
        return self

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __exit__(self, *_):
        self.close()
