"""Progress bars for long steps (db-pull, db-import): one line on a terminal, redrawn in place.

On a terminal (stderr) a bar shows the share done, the amounts and the time left; without a total it
shows the amount and the time so far. Anywhere else (CI, a log) it writes one line when the step ends,
so a log does not fill with redraws. Nothing here touches the data being measured.
"""

import sys
import time

WIDTH = 28


def amount(value):
    """Bytes as text: 512 B, 3.4 MB, 1.25 GB."""
    value = float(value)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.2f} {unit}' if unit == 'GB' else f'{value:.1f} {unit}'
        value /= 1024
    return ''


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'


class Bar:
    """bar = Bar('Download', total); bar.update(done) or bar.advance(count); bar.finish()."""

    def __init__(self, label, total=0, stream=None, terminal=None):
        self.label, self.total, self.done = label, max(0, int(total or 0)), 0
        self.stream = stream or sys.stderr
        self.terminal = self.stream.isatty() if terminal is None else terminal
        self.started = time.monotonic()
        self.drawn = 0.0
        self.finished = False
        if self.terminal:
            self.draw(force=True)

    def advance(self, count):
        self.update(self.done + count)

    def update(self, done, total=None):
        if total is not None:
            self.total = max(0, int(total))
        self.done = max(0, int(done))
        if self.terminal:
            self.draw()

    def text(self):
        elapsed = time.monotonic() - self.started
        if self.total:
            share = min(self.done / self.total, 1.0)
            filled = int(share * WIDTH)
            left = (elapsed / share - elapsed) if share > 0.01 else None
            tail = f'{duration(left)} left' if left is not None and share < 1 else duration(elapsed)
            return (f'{self.label:<24} [{"#" * filled}{"." * (WIDTH - filled)}] {share:4.0%}  '
                    f'{amount(self.done)} / {amount(self.total)}  {tail}')
        return f'{self.label:<24} {amount(self.done)}  {duration(elapsed)}'

    def draw(self, force=False):
        now = time.monotonic()
        if not force and now - self.drawn < 0.2:
            return
        self.drawn = now
        self.stream.write('\r\033[K' + self.text())
        self.stream.flush()

    def finish(self, note=''):
        if self.finished:
            return
        self.finished = True
        if self.total and self.done < self.total:
            self.done = self.total if not note else self.done
        if self.terminal:
            self.stream.write('\r\033[K' + self.text() + (f'  {note}' if note else '') + '\n')
        else:
            self.stream.write(f'{self.label}: {amount(self.done)} in {duration(time.monotonic() - self.started)}'
                              + (f' ({note})' if note else '') + '\n')
        self.stream.flush()

    def __enter__(self):
        return self

    def __exit__(self, kind, error, trace):
        self.finish('failed' if kind else '')


def step(number, count, text, stream=None):
    """A heading for one step of a longer command: ==> [2/4] Download."""
    stream = stream or sys.stderr
    stream.write(f'==> [{number}/{count}] {text}\n')
    stream.flush()
