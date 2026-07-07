"""The app-close guard: closing the window while a backup runs must ask first
(pywebview `closing` handler - returning False keeps the window open). Fully
synthetic: fake jobs + a fake window, no GUI."""
from backupviewer.api import Api


class _Job:
    def __init__(self, status):
        self._s = status

    def snapshot(self):
        return {"status": self._s}


class _Win:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def create_confirmation_dialog(self, title, message):
        self.asked.append((title, message))
        return self.answer


def test_close_allowed_when_idle():
    api = Api()
    api._window = _Win(False)
    api._jobs["a"] = _Job("done")
    api._jobs["b"] = _Job("cancelled")
    assert api._confirm_close() is True
    assert api._window.asked == []                 # no dialog when nothing is running


def test_close_asks_and_respects_no():
    api = Api()
    api._window = _Win(False)
    api._jobs["a"] = _Job("downloading")
    assert api._confirm_close() is False           # user said no -> stay open
    assert len(api._window.asked) == 1
    assert "1 backup still running" in api._window.asked[0][1]


def test_close_asks_and_respects_yes():
    api = Api()
    api._window = _Win(True)
    api._jobs["a"] = _Job("connecting")
    api._jobs["b"] = _Job("downloading")
    api._jobs["c"] = _Job("done")
    assert api._confirm_close() is True
    assert "2 backups still running" in api._window.asked[0][1]


def test_close_never_traps_on_dialog_failure():
    class _Boom:
        def create_confirmation_dialog(self, *a):
            raise RuntimeError("gui already torn down")

    api = Api()
    api._window = _Boom()
    api._jobs["a"] = _Job("downloading")
    assert api._confirm_close() is True            # fail OPEN - never trap the user
