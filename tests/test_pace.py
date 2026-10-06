"""0.37.6: background work waits while a video plays."""
import threading
import time

from lunelis import pace


def test_background_work_waits_while_a_video_plays():
    done = []
    pace.set_playing(True)
    t = threading.Thread(target=lambda: (pace.breathe(), done.append(time.monotonic())))
    t.start()
    time.sleep(0.3)
    assert not done                         # still waiting
    pace.set_playing(False)
    t.join(2)
    assert done


def test_breathe_returns_when_asked_to_stop_and_on_the_window_thread():
    pace.set_playing(True)
    try:
        pace.breathe()                      # the window's own thread never waits
        out = []
        t = threading.Thread(target=lambda: (pace.breathe(lambda: True), out.append(1)))
        t.start()
        t.join(2)
        assert out
    finally:
        pace.set_playing(False)
