"""Report setup stages on stderr, independently of the final result."""
import sys
import time


def size_text(size):
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if size < 1024 or unit == 'GiB':
            return f'{size:.1f} {unit}'
        size /= 1024


class SetupProgress:
    def __init__(self, stream=None, enabled=True):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = enabled
        self.tty = self.stream.isatty()
        self.previous = None
        self.in_line = False

    def finish(self):
        if self.in_line:
            print(file=self.stream, flush=True)
            self.in_line = False

    def stage(self, message):
        if self.enabled:
            self.finish()
            print(message, file=self.stream, flush=True)

    def transfer(self, name, current, total, *, complete=False):
        if not self.enabled:
            return
        now = time.monotonic()
        previous = self.previous
        if previous is None or previous[0] != name or current < previous[1]:
            self.previous = (name, current, now)
            speed = 0
        else:
            elapsed = now - previous[2]
            if not complete and elapsed < (.35 if self.tty else 5):
                return
            speed = (current - previous[1]) / elapsed if elapsed else 0
            self.previous = (name, current, now)
        fraction = min(1, current / total) if total else 1
        count = int(fraction * 20)
        seconds = int((total - current) / speed) if speed > 0 else None
        eta = '—' if seconds is None else f'{seconds // 60}:{seconds % 60:02d}'
        rate = f'{size_text(speed)}/s' if speed else '—'
        bar = f'[{"#" * count}{"-" * (20-count)}] ' if self.tty else ''
        prefix = '\r\033[K' if self.tty else ''
        print(f'{prefix}{name} {bar}{fraction:5.1%}  {size_text(current)} / {size_text(total)}  {rate}  ETA {eta}',
              end='' if self.tty else '\n', file=self.stream, flush=True)
        self.in_line = self.tty
        if complete:
            self.finish()
            self.previous = None
