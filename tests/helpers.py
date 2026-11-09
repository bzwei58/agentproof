"""Test fixture cleanup tolerant of a late local Git-hook installer."""
import errno
import time


def cleanup_temp(directory):
    for attempt in range(3):
        try:
            directory.cleanup()
            return
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY or attempt == 2:
                raise
            time.sleep(0.1)
