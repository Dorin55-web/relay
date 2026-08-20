"""A picture of another application's window, for the phone.

The card the phone gets when a step finishes is built by diffing what the
window said before against what it says after, read through whichever profile
recognises it. That is a reconstruction, and a reconstruction is the thing you
trust least at the moment you are asking whether something worked. A picture is
the window itself, and it needs no profile to be right.

Three things make it less obvious than it looks.

**It has to run on the thread that owns the windows.** Qt refuses a grab from
anywhere else, and the bot runs on its own thread. `capture_on` puts the work
on the thread the given object lives on and waits for the answer - the same
hop __main__ uses to close the orb when the phone asks it to.

**GetWindowRect answers in physical pixels and QScreen.grabWindow takes logical
ones.** Measured on this machine, at 125%: a full-screen window is 1920x1080 to
Windows and 1536x864 to Qt. Hand Windows' numbers to Qt and the picture is of a
rectangle a quarter of the way down and across from the window, running off the
edge of the screen - a photograph of the wrong thing, which reads as the window
having moved rather than as a bug here.

**The desktop is grabbed, not the window's own device context.** Every
application this is ever aimed at draws through the GPU, and a GPU-composited
window hands back a black DC. Reading the composed screen is what actually
produces a picture. The cost is that whatever sits over the window is in the
picture too - which is what the screen looks like, and is said out loud rather
than hidden.
"""

import ctypes
import threading

from .target import monitor_rect, window_rect

# The long edge of the picture that goes out. Telegram re-encodes a photo at
# its end anyway and a phone is about a thousand pixels across, so a 4K window
# sent whole would be several megabytes of upload - over mobile data, for
# detail nobody can see - and past the size Telegram accepts at all.
MAX_EDGE = 1600

# How long the phone waits for the GUI thread to answer. It has to finish the
# frame it is painting first, and a grab of a large window is tens of
# milliseconds. Past this it is wedged rather than busy, and being told that is
# worth more than waiting for a picture that is not coming.
WAIT_SECONDS = 8


def logical_rect(bounds, monitor, origin, ratio):
    """Where Qt thinks a window is, given where Windows says it is.

    `bounds` and `monitor` are physical pixels - a window and the screen it
    sits on, both from Windows. `origin` and `ratio` are that same screen's
    top-left and scale factor as Qt has them. Qt anchors each screen's logical
    origin to its physical one, so the offset from the corner of the monitor is
    the part that gets divided, not the coordinate itself: on a second monitor
    starting at x=1920 the two spaces do not share an origin to divide from.

    Clamped to the monitor, because a maximised window overhangs it by the
    width of its invisible resize border - eight or nine pixels on every side -
    and a grab of that overhang comes back as a black band down the picture.
    """
    left = max(bounds[0], monitor[0])
    top = max(bounds[1], monitor[1])
    right = min(bounds[2], monitor[2])
    bottom = min(bounds[3], monitor[3])
    return (round(origin[0] + (left - monitor[0]) / ratio),
            round(origin[1] + (top - monitor[1]) / ratio),
            round((right - left) / ratio),
            round((bottom - top) / ratio))


def _screen_named(name):
    """The QScreen Windows calls `name`, or the primary one.

    Paired by name rather than by position: position is the very thing being
    converted here, so using it to choose the screen that defines the
    conversion would be circular. Windows and Qt both call it `\\\\.\\DISPLAY1`.
    """
    from PySide6.QtGui import QGuiApplication

    for screen in QGuiApplication.screens():
        if screen.name() == name:
            return screen
    return QGuiApplication.primaryScreen()


def capture(hwnd):
    """One picture of `hwnd`, as PNG bytes.

    Returns (bytes, None), or (None, why not) - the second half is written to
    be read on a phone, because that is where it ends up.

    Must be called on the thread that owns the windows; see capture_on, which
    is how the phone's thread gets here.
    """
    from PySide6.QtCore import QBuffer, QIODevice, Qt

    user32 = ctypes.windll.user32
    if not hwnd or not user32.IsWindow(hwnd):
        return None, "that window has closed"
    if user32.IsIconic(hwnd):
        # A minimised window is not drawn anywhere, and its rectangle is off
        # at -32000: grabbing it would send back a rectangle of desktop and
        # call it the window. Saying so is the only honest answer.
        return None, "it is minimised, so there is nothing on screen to see"

    bounds = window_rect(hwnd)
    screen = monitor_rect(hwnd)
    if not bounds or not screen:
        return None, "Windows would not say where that window is"

    name, monitor = screen
    qscreen = _screen_named(name)
    if qscreen is None:
        return None, "there is no screen to photograph"

    where = qscreen.geometry()
    x, y, width, height = logical_rect(
        bounds, monitor, (where.x(), where.y()), qscreen.devicePixelRatio())
    if width < 1 or height < 1:
        return None, "that window has no size on screen"

    picture = qscreen.grabWindow(0, x, y, width, height)
    if picture.isNull() or picture.width() < 1:
        return None, "the screen would not give up a picture of it"
    if max(picture.width(), picture.height()) > MAX_EDGE:
        picture = picture.scaled(MAX_EDGE, MAX_EDGE, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not picture.save(buffer, "PNG"):
        return None, "the picture could not be encoded"
    return bytes(buffer.data()), None


def capture_on(receiver, hwnd, seconds=WAIT_SECONDS):
    """Take the picture on the thread `receiver` lives on, and wait for it.

    singleShot with an object as its second argument runs the call on that
    object's thread rather than this one, which is what makes it legal to grab
    at all. It is the same hop that closes the orb when /restart arrives; this
    one differs in having to bring something back, which is what the event and
    the box are for.

    Waiting rather than handing back a callback, because at the other end of it
    is a person holding a phone. The timeout is what stops that person waiting
    for ever when the GUI thread is the thing that has gone wrong - which is
    the state /restart exists for, and one they cannot diagnose from a room
    away without being told.
    """
    from PySide6.QtCore import QTimer

    done = threading.Event()
    answer = {}

    def grab():
        try:
            answer["it"] = capture(hwnd)
        except Exception as exc:
            answer["it"] = (None, f"the grab itself failed ({exc})")
        finally:
            done.set()

    QTimer.singleShot(0, receiver, grab)
    if not done.wait(seconds):
        return None, (f"the window thread did not answer within {seconds} "
                      f"seconds; it may be stuck. /restart is the way out")
    return answer["it"]
