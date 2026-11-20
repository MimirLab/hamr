"""Wall-clock budgets do not reset when a connection produces another byte."""
from contextlib import contextmanager
import threading
from .core import Error


@contextmanager
def wall_budget(seconds,abort,code,message):
    expired=threading.Event()
    def finish():
        expired.set();abort()
    timer=threading.Timer(seconds,finish);timer.daemon=True;timer.start()
    try:
        try:yield
        except Exception as exc:
            if expired.is_set():raise Error(code,message,'Resume the saved job; verified takes are retained.') from exc
            raise
        if expired.is_set():raise Error(code,message,'Resume the saved job; verified takes are retained.')
    finally:
        timer.cancel();timer.join()
