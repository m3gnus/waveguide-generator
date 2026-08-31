"""Remove the macOS title bar, and let the application's top bar be one.

The Windows half of this problem lives in ``launchers.windowframe``. macOS needs
a different answer to every part of it, because AppKit hands out its title bar on
completely different terms from Win32.

What AppKit already does well is the removal. ``NSFullSizeContentViewWindowMask``
with ``titlebarAppearsTransparent`` extends the content view over the title bar
without touching the style mask bits that carry resizing, so the eight resize
grips, the zoom animation and the window shadow all survive -- the exact things
``frameless=True`` destroys on Windows. Probed on a live 900x600 pywebview
window: every point below y=4 hit-tests to the web view, y=2 still hit-tests to
``NSThemeFrame`` so the top edge stays a resize grip, and ``window.innerHeight``
becomes the full window height.

What it does not do is put the traffic lights anywhere useful. They are pinned at
y=6..22 measured from the window's top, whatever the interface looks like, and WG's
top bar is 48 px tall -- so the real buttons would sit 10 px above its centre,
reading as a misalignment rather than a design. Three things were measured and
rejected before settling on drawing them in the interface instead:

* An ``NSTitlebarAccessoryViewController`` grows the title bar (56 px with
  ``layoutAttribute`` bottom) but leaves the buttons at y=6. AppKit does not
  re-centre them.
* ``setFrameOrigin_`` on each button does move them, and does not survive: the
  next resize or zoom puts all three back at y=6. Holding them down would mean
  re-applying on every frame of a live resize.
* ``layoutAttribute`` left and top change nothing at all.

So the standard buttons are hidden and ``WindowControls`` draws its own, which is
the arrangement the interface was already written for. The one thing that costs
is the green button's press-and-hold menu; full screen remains on Ctrl-Cmd-F.

Dragging then has to be built, and there is no CSS route to it. WebKit does not
implement ``-webkit-app-region`` at all -- ``CSS.supports('-webkit-app-region',
'drag')`` is false in this WKWebView -- so the stylesheet's drag region is a
Windows-only mechanism no matter what it claims. ``performWindowDragWithEvent_``
is the replacement, and it is strictly better than a hand-rolled drag: AppKit
runs its own event loop for the gesture, so moving between Spaces and displays,
and the snapping that comes with them, arrive without code implementing any of it.
The interface calls ``window_begin_drag`` on a mousedown that is not over a
control, and this module hands the in-flight event to AppKit.

Everything here is best-effort and reports whether it worked, because the
interface draws its buttons on that answer alone. A failure anywhere leaves the
window with the title bar it already had rather than one that cannot be moved.

The AppKit import is deferred to call time throughout: ``launchers.desktop``
imports this module on Windows and Linux too, where pyobjc is not installed.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, NamedTuple

log = logging.getLogger("wg.launch.frame.macos")

#: ``NSWindowStyleMaskFullSizeContentView``. Named here rather than read from
#: AppKit so the arithmetic below can be tested on a machine without it.
FULL_SIZE_CONTENT_VIEW = 1 << 15
#: ``NSWindowTitleHidden``.
TITLE_HIDDEN = 1
#: ``NSWindowStyleMaskFullScreen``.
FULL_SCREEN = 1 << 14
#: ``NSWindowCollectionBehaviorFullScreenPrimary``. Without it
#: ``toggleFullScreen:`` does nothing at all, silently.
FULL_SCREEN_PRIMARY = 1 << 7

#: ``NSApplicationPresentationOptions``, for full screen.
#:
#: macOS's own full screen only *auto-hides* the menu bar: it slides back down
#: whenever the pointer reaches the top of the screen, over whatever is there --
#: here the top bar, with the brand, the menus and Solve underneath it.
#:
#: Setting these after ``DidEnterFullScreen`` does not work: AppKit put them
#: back to its own 1029 (``FullScreen | AutoHideMenuBar | AutoHideDock``) within
#: five seconds when it was tried. The one hook AppKit does honour is the window
#: delegate's ``window:willUseFullScreenPresentationOptions:``, which is asked
#: for the options rather than told them -- see :func:`hide_menu_bar_in_full_screen`.
PRESENTATION_AUTO_HIDE_DOCK = 1 << 0
PRESENTATION_HIDE_DOCK = 1 << 1
PRESENTATION_AUTO_HIDE_MENU_BAR = 1 << 2
PRESENTATION_HIDE_MENU_BAR = 1 << 3

#: What the delegate answers with: whatever AppKit proposed, with the two
#: auto-hide bits swapped for their unconditional forms. Building on the
#: proposal rather than replacing it keeps every other bit AppKit asked for.
PRESENTATION_DROP = PRESENTATION_AUTO_HIDE_DOCK | PRESENTATION_AUTO_HIDE_MENU_BAR
PRESENTATION_ADD = PRESENTATION_HIDE_DOCK | PRESENTATION_HIDE_MENU_BAR

ENTER_FULL_SCREEN = "NSWindowDidEnterFullScreenNotification"
EXIT_FULL_SCREEN = "NSWindowDidExitFullScreenNotification"

#: The view class AppKit puts the caption in. pywebview paints this one opaque,
#: and it is the only thing standing between a transparent title bar and a
#: visibly transparent one.
TITLEBAR_CONTAINER = "NSTitlebarContainerView"

#: pyobjc hands back a KVO subclass for any view something is observing, and its
#: class name carries this prefix. The theme frame came back as
#: ``NSKVONotifying_NSThemeFrame`` in the live probe, so matching a bare class
#: name would be a check that works until the day something observes the view.
KVO_PREFIX = "NSKVONotifying_"

CLOSE_BUTTON = 0
MINIATURIZE_BUTTON = 1
ZOOM_BUTTON = 2

#: The events ``performWindowDragWithEvent_`` will start a drag from. The click
#: reaches us through the JavaScript bridge, so by the time this runs the
#: application's current event may already have become the first drag of the
#: gesture; both are the same gesture and both are accepted. A mouse-up is not:
#: the gesture is over, and starting a drag from it would move the window on a
#: click the user has already finished.
DRAG_EVENT_TYPES = frozenset({1, 6})  # NSEventTypeLeftMouseDown, …LeftMouseDragged

#: What macOS does when a title bar is double-clicked, from System Settings.
#: Unset means Zoom, which is the shipped default.
DOUBLE_CLICK_DEFAULTS_KEY = "AppleActionOnDoubleClick"

#: How long a bridge call will wait for the AppKit main thread before giving up.
#: Only reads wait at all, and a read that cannot be answered is a state the
#: interface refreshes anyway.
MAIN_THREAD_TIMEOUT = 5.0


class Rect(NamedTuple):
    """A window rectangle, in AppKit's bottom-left origin."""

    x: float
    y: float
    width: float
    height: float


def height_to_give_back(before: Rect, after: Rect, *, zoomed: bool) -> Rect | None:
    """Undo the shrink that adopting a full-size content view causes.

    Setting the mask keeps the *content view* the size it was and takes the title
    bar's height off the window instead: a 900x600 window measured 900x572 the
    moment the mask went on. Left alone the window would lose 28 px every time
    the app starts, which over a few releases is a window that has quietly
    shrunk. Restoring the frame exactly puts the height back on the bottom edge
    and leaves the top edge where the user last put it.

    A zoomed window is left alone: its frame is the screen's, not the user's, and
    growing it would push it off the bottom of the display.
    """

    if zoomed or after.height >= before.height:
        return None
    return before


def double_click_action(preference: str | None) -> str:
    """Map the system's title-bar double-click setting onto what to do.

    Honouring this rather than always zooming matters more here than it looks:
    with the OS title bar gone, the top bar is the only surface left that can
    answer the gesture at all, so getting it wrong removes the behaviour from the
    window entirely rather than merely from one strip of it.
    """

    match (preference or "").strip().casefold():
        case "minimize":
            return "minimize"
        case "none":
            return "none"
        case _:
            return "zoom"


def _appkit() -> Any:
    import AppKit

    return AppKit


def on_main(fn: Callable[[], Any], *, wait: bool = True, timeout: float = MAIN_THREAD_TIMEOUT) -> Any:
    """Run ``fn`` on the thread AppKit allows window geometry to be touched from.

    Every call into this module arrives on a thread pywebview spawned for the
    JavaScript bridge, and AppKit raises ``NSInternalInconsistencyException`` --
    "NSWindow geometry should only be modified on the main thread" -- rather than
    misbehaving quietly. It was raised during development, which is why this
    exists rather than a bare call.

    ``wait=False`` is not an optimization. ``performWindowDragWithEvent_`` runs
    its own event loop until the user lets go of the mouse, so waiting for it
    would hold the bridge thread for the whole drag.
    """

    import Foundation

    if Foundation.NSThread.isMainThread():
        return fn()

    box: dict[str, Any] = {}
    done = threading.Event()

    def block() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc
        finally:
            done.set()

    Foundation.NSOperationQueue.mainQueue().addOperationWithBlock_(block)
    if not wait:
        return None
    if not done.wait(timeout):
        raise TimeoutError("The AppKit main thread did not answer in time")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class MacFrame:
    """One macOS window whose title bar the interface has taken over."""

    def __init__(self, window: Any) -> None:
        self.window = window
        # The observer tokens are kept because releasing them unregisters the
        # blocks, and a window that stops hearing about full screen would leave
        # the menu bar hidden after leaving it.
        self._observers: list[Any] = []

    # -- installation -------------------------------------------------------

    def install(self) -> None:
        """Extend the content over the title bar and hide the traffic lights."""

        appkit = _appkit()
        before = self._frame()
        zoomed = bool(self.window.isZoomed())
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(TITLE_HIDDEN)
        self.window.setStyleMask_(self.window.styleMask() | FULL_SIZE_CONTENT_VIEW)
        for button in (CLOSE_BUTTON, MINIATURIZE_BUTTON, ZOOM_BUTTON):
            control = self.window.standardWindowButton_(button)
            if control is not None:
                control.setHidden_(True)
        self._clear_titlebar_background(appkit)
        # The green button promises full screen, so the window has to be allowed
        # into it. Without this collection behaviour toggleFullScreen: is a
        # no-op that reports nothing.
        self.window.setCollectionBehavior_(
            self.window.collectionBehavior() | FULL_SCREEN_PRIMARY
        )
        self._observe_full_screen()
        restore = height_to_give_back(before, self._frame(), zoomed=zoomed)
        if restore is not None:
            self.window.setFrame_display_(
                appkit.NSMakeRect(restore.x, restore.y, restore.width, restore.height), True
            )

    def _clear_titlebar_background(self, appkit: Any) -> None:
        """Stop the title bar painting over the top bar now underneath it.

        ``titlebarAppearsTransparent`` asks AppKit not to draw its own material.
        It does not, and cannot, undo a background colour somebody set by hand --
        and pywebview sets one: every non-frameless window gets
        ``contentView().superview().subviews().lastObject().setBackgroundColor_(
        windowBackgroundColor)``, with the comment that it stops the title bar
        changing with the window colour.

        Left alone that is a 28 px opaque band across the top of the window with
        the application's own top bar hidden behind it -- the brand line gone,
        the toolbar cut in half, and the window controls sliced through the
        middle. It looks like a layout bug in the interface and is not one, which
        is why it is worth this much comment: the transparency call succeeds,
        reports success, and changes nothing visible.
        """

        clear = appkit.NSColor.clearColor()
        for view in self.window.contentView().superview().subviews():
            if type(view).__name__.removeprefix(KVO_PREFIX) != TITLEBAR_CONTAINER:
                continue
            for target in (view, *view.subviews()):
                setter = getattr(target, "setBackgroundColor_", None)
                if setter is not None:
                    setter(clear)

    def _observe_full_screen(self) -> None:
        """Put the title bar back the way we want it after a full-screen change.

        Entering and leaving full screen rebuilds the window's title bar, and
        the rebuilt one is AppKit's own: opaque, with its buttons back. In full
        screen it is the strip that slides down when the pointer reaches the top
        of the screen, so it is exactly where it is most visible.

        The notifications rather than our own toggle, because full screen is not
        only ours to start: Ctrl-Cmd-F, the Window menu and the green button in
        the strip itself all reach it, and treatment applied on only one of
        those paths is treatment that is missing on the rest.
        """

        try:
            import Foundation

            centre = Foundation.NSNotificationCenter.defaultCenter()
            queue = Foundation.NSOperationQueue.mainQueue()
            self._observers = [
                centre.addObserverForName_object_queue_usingBlock_(
                    name, self.window, queue, handler
                )
                for name, handler in (
                    (ENTER_FULL_SCREEN, lambda note: self.reapply()),
                    (EXIT_FULL_SCREEN, lambda note: self.reapply()),
                )
            ]
        except Exception:  # noqa: BLE001 - full screen still works, with its menu bar
            log.exception("Full screen will keep the menu bar: the observers failed")

    def reapply(self) -> None:
        """Re-hide the buttons and re-clear the background, whatever AppKit did."""

        try:
            appkit = _appkit()
            self.window.setTitlebarAppearsTransparent_(True)
            self.window.setTitleVisibility_(TITLE_HIDDEN)
            for button in (CLOSE_BUTTON, MINIATURIZE_BUTTON, ZOOM_BUTTON):
                control = self.window.standardWindowButton_(button)
                if control is not None:
                    control.setHidden_(True)
            self._clear_titlebar_background(appkit)
        except Exception:  # noqa: BLE001 - cosmetic, and never worth a crash
            log.exception("Could not re-apply the custom frame after a full-screen change")

    def menu_bar_is_hidden(self) -> bool:
        try:
            appkit = _appkit()
            options = appkit.NSApplication.sharedApplication().presentationOptions()
            return bool(int(options) & PRESENTATION_HIDE_MENU_BAR)
        except Exception:  # noqa: BLE001
            return False

    def _frame(self) -> Rect:
        frame = self.window.frame()
        return Rect(
            float(frame.origin.x),
            float(frame.origin.y),
            float(frame.size.width),
            float(frame.size.height),
        )

    # -- state and actions --------------------------------------------------

    def maximized(self) -> bool:
        """Whether the window is zoomed, which is macOS's word for maximized.

        This is the Option-click state. What the button reports to the interface
        is :meth:`fullscreen`, because that is what the glyph on it describes.

        pywebview's own ``maximize``/``restore`` pair cannot answer this: on
        Cocoa ``maximize`` resizes to the screen and ``restore`` calls
        ``deminiaturize_``, so they are not each other's inverse and a window
        maximized through them can never be restored. Everything below goes to
        AppKit directly for that reason.
        """

        return bool(on_main(lambda: bool(self.window.isZoomed())))

    def minimize(self) -> None:
        on_main(lambda: self.window.miniaturize_(None), wait=False)

    def top_inset(self) -> float:
        """How far down the interface must start to clear the menu bar.

        Full screen is the only case, and AppKit does not answer for it: with
        the content view full-size, ``contentLayoutRect`` and
        ``safeAreaInsets.top`` both report 0 there (measured -- windowed they
        report the 28 px title bar). The menu bar is not part of the window at
        all; it slides down over whatever is beneath it when the pointer reaches
        the top of the screen, and what is beneath it here is the top bar, whose
        brand, menus and Solve button all end up under it.

        So the height comes from the menu itself. Reserving it costs 24 px of a
        full screen and buys a top bar that can always be clicked.
        """

        if not self.fullscreen() or self.menu_bar_is_hidden():
            # Nothing slides down over a menu bar that is not there.
            return 0.0
        try:
            appkit = _appkit()
            menu = appkit.NSApplication.sharedApplication().mainMenu()
            height = float(menu.menuBarHeight()) if menu is not None else 0.0
        except Exception:  # noqa: BLE001 - a nicety, not a requirement
            return 0.0
        # A menu bar that reports nothing is one we should not reserve for.
        return height if 0.0 < height < 64.0 else 0.0

    def fullscreen(self) -> bool:
        return bool(on_main(lambda: bool(self.window.styleMask() & FULL_SCREEN)))

    def toggle_fullscreen(self) -> bool:
        """Enter or leave full screen, and report where the window is going."""

        target = not self.fullscreen()
        on_main(lambda: self.window.toggleFullScreen_(None), wait=False)
        return target

    def toggle_zoom(self) -> bool:
        """Zoom or unzoom -- what Option-clicking the green button does."""

        target = not self.maximized()
        on_main(lambda: self.window.zoom_(None), wait=False)
        return target

    def begin_drag(self) -> None:
        """Hand the click that is in flight to AppKit, and let it run the drag."""

        def drag() -> None:
            appkit = _appkit()
            event = appkit.NSApplication.sharedApplication().currentEvent()
            if event is None or int(event.type()) not in DRAG_EVENT_TYPES:
                return
            self.window.performWindowDragWithEvent_(event)

        on_main(drag, wait=False)

    def double_click(self) -> str:
        """Answer a double-click on the top bar the way the title bar would."""

        action = double_click_action(self.double_click_preference())
        if action == "zoom":
            on_main(lambda: self.window.zoom_(None), wait=False)
        elif action == "minimize":
            on_main(lambda: self.window.miniaturize_(None), wait=False)
        return action

    def double_click_preference(self) -> str | None:
        try:
            appkit = _appkit()
            defaults = appkit.NSUserDefaults.standardUserDefaults()
            value = defaults.stringForKey_(DOUBLE_CLICK_DEFAULTS_KEY)
            return None if value is None else str(value)
        except Exception:  # noqa: BLE001 - a preference, not a requirement
            return None


def full_screen_presentation(proposed: int) -> int:
    """The presentation options full screen should really use.

    ``HideMenuBar`` and ``HideDock`` rather than their auto-hide forms, so
    neither slides back over the interface when the pointer reaches the top of
    the screen. AppKit refuses ``HideMenuBar`` without ``HideDock``, which is
    why the Dock goes too.
    """

    return (int(proposed) & ~PRESENTATION_DROP) | PRESENTATION_ADD


def hide_menu_bar_in_full_screen() -> bool:
    """Teach pywebview's window delegate to ask for a menu-bar-free full screen.

    ``window:willUseFullScreenPresentationOptions:`` is the only route AppKit
    honours, and the delegate is pywebview's ``BrowserView.WindowDelegate``, so
    the method is added to that class rather than to a delegate of our own.
    Replacing the delegate is the alternative and a much worse one: pywebview
    routes window closing, resizing and full-screen bookkeeping through it, and
    a proxy that forwards all of that is far more to get wrong than one method
    that AppKit calls once per transition and that has no state.

    Adding it twice is not an error -- the second call finds it already there.
    """

    if sys.platform != "darwin":
        return False
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        delegate = BrowserView.WindowDelegate
        selector = b"window:willUseFullScreenPresentationOptions:"
        if delegate.instancesRespondToSelector_(selector):
            return True

        def window_willUseFullScreenPresentationOptions_(self, window, proposed):
            return full_screen_presentation(proposed)

        objc.classAddMethods(
            delegate,
            [
                objc.selector(
                    window_willUseFullScreenPresentationOptions_,
                    selector=selector,
                    signature=b"Q@:@Q",
                )
            ],
        )
    except Exception:  # noqa: BLE001 - full screen still works, with its menu bar
        log.exception("Full screen will keep its menu bar: the delegate was not extended")
        return False
    return True


def install_custom_frame(native_window: Any) -> MacFrame | None:
    """Take the title bar from ``native_window``, or leave the window as it was.

    ``native_window`` is the ``NSWindow`` pywebview publishes as ``Window.native``.
    """

    if sys.platform != "darwin":
        return None
    if native_window is None:
        log.info("Keeping the OS title bar: pywebview published no native window")
        return None
    hide_menu_bar_in_full_screen()
    try:
        frame = MacFrame(native_window)
        on_main(frame.install)
    except Exception:  # noqa: BLE001 - a native boundary, and a losable feature
        log.exception("Keeping the OS title bar: the custom frame could not be installed")
        return None
    return frame
