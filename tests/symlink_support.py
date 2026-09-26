"""Skip real symlink tests only when the platform cannot create their fixture."""
import errno
import unittest


def create_symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314 or exc.errno in {errno.ENOSYS, errno.ENOTSUP}:
            raise unittest.SkipTest("Symlink creation is unavailable for this OS/account") from exc
        raise
