"""The decisions behind the macOS window frame, without an NSWindow to make them on.

The AppKit calls in ``launchers.macoswindow`` were measured against a live
pywebview window; what is worth pinning here is what surrounds them, because each
of these is a way for the window to look right and behave wrong -- a window that
loses 28 px of height every time it starts, a maximize button that can maximize
but never restore, or a drag started from the mouse-up that ended it.

The module must also import on Windows and Linux, where pyobjc does not exist.
``launchers.desktop`` imports it unconditionally and the suite runs on all three,
so that is a real constraint rather than a stylistic one -- and this file is what
proves it, simply by importing.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from launchers.macoswindow import (
    DRAG_EVENT_TYPES,
    PRESENTATION_AUTO_HIDE_DOCK,
    PRESENTATION_AUTO_HIDE_MENU_BAR,
    PRESENTATION_HIDE_DOCK,
    PRESENTATION_HIDE_MENU_BAR,
    full_screen_presentation,
    FULL_SIZE_CONTENT_VIEW,
    MacFrame,
    Rect,
    double_click_action,
    height_to_give_back,
    install_custom_frame,
)

#: The 900x600 window the live probe used, and the 900x572 AppKit turned it into
#: the moment the full-size content view went on.
BEFORE = Rect(100.0, 100.0, 900.0, 600.0)
AFTER = Rect(100.0, 100.0, 900.0, 572.0)


class TestHeightToGiveBack:
    # Adopting the mask keeps the *content view* the size it was and takes the
    # title bar off the window. Unnoticed, that is 28 px lost per launch.
    def test_the_title_bar_height_is_returned_to_the_window(self) -> None:
        assert height_to_give_back(BEFORE, AFTER, zoomed=False) == BEFORE

    def test_a_window_that_did_not_shrink_is_left_alone(self) -> None:
        assert height_to_give_back(BEFORE, BEFORE, zoomed=False) is None

    # A zoomed window's frame is the screen's. Growing it by the title bar would
    # push the bottom of the window off the bottom of the display.
    def test_a_zoomed_window_is_left_alone(self) -> None:
        assert height_to_give_back(BEFORE, AFTER, zoomed=True) is None


class TestDoubleClickAction:
    # Unset is the shipped default, and the shipped default is Zoom.
    @pytest.mark.parametrize("preference", [None, "", "Maximize", "unrecognised"])
    def test_anything_unrecognised_zooms(self, preference: str | None) -> None:
        assert double_click_action(preference) == "zoom"

    @pytest.mark.parametrize("preference", ["Minimize", "minimize", " MINIMIZE "])
    def test_minimize_is_honoured_however_it_is_spelled(self, preference: str) -> None:
        assert double_click_action(preference) == "minimize"

    def test_none_means_none(self) -> None:
        assert double_click_action("None") == "none"


class FakeButton:
    def __init__(self) -> None:
        self.hidden = False

    def setHidden_(self, value: bool) -> None:  # noqa: N802 - an AppKit selector
        self.hidden = value


class FakeView:
    """A view that records the last background colour anyone set on it."""

    def __init__(self, name: str, subviews: list["FakeView"] | None = None) -> None:
        self._name = name
        self._subviews = subviews or []
        self.background: object = "opaque"

    def subviews(self) -> list["FakeView"]:
        return self._subviews

    def setBackgroundColor_(self, colour: object) -> None:  # noqa: N802
        self.background = colour


class NSTitlebarContainerView(FakeView):  # noqa: N801 - the AppKit name is the point
    """Named for its class, because that is how the frame finds it."""


class NSKVONotifying_NSTitlebarContainerView(NSTitlebarContainerView):  # noqa: N801
    """What pyobjc hands back once anything observes the view."""


class FakeContentView(FakeView):
    def __init__(self, theme: FakeView) -> None:
        super().__init__("content")
        self._theme = theme

    def superview(self) -> FakeView:
        return self._theme


class FakeWindow:
    """Enough NSWindow to record what the frame did to it."""

    def __init__(
        self,
        *,
        frame: Rect = BEFORE,
        shrink: float = 28.0,
        zoomed: bool = False,
        titlebar_class: type[FakeView] = NSTitlebarContainerView,
    ) -> None:
        self._frame = frame
        self._shrink = shrink
        self.zoomed = zoomed
        self.style = 15
        self.transparent = False
        self.title_visibility: int | None = None
        self.buttons = {index: FakeButton() for index in range(3)}
        self.calls: list[str] = []
        self.dragged_with: object | None = None
        self.behaviour = 0
        self.titlebar = titlebar_class("titlebar", [FakeView("caption")])
        self._theme = FakeView("theme", [FakeView("other"), self.titlebar])
        self._content = FakeContentView(self._theme)

    def contentView(self) -> FakeContentView:  # noqa: N802
        return self._content

    # -- reads
    def frame(self) -> object:
        return SimpleNamespace(
            origin=SimpleNamespace(x=self._frame.x, y=self._frame.y),
            size=SimpleNamespace(width=self._frame.width, height=self._frame.height),
        )

    def styleMask(self) -> int:  # noqa: N802
        return self.style

    def isZoomed(self) -> bool:  # noqa: N802
        return self.zoomed

    def standardWindowButton_(self, index: int) -> FakeButton:  # noqa: N802
        return self.buttons[index]

    # -- writes
    def setTitlebarAppearsTransparent_(self, value: bool) -> None:  # noqa: N802
        self.transparent = value

    def setTitleVisibility_(self, value: int) -> None:  # noqa: N802
        self.title_visibility = value

    def setStyleMask_(self, value: int) -> None:  # noqa: N802
        self.style = value
        # AppKit keeps the content view and takes the height off the window.
        self._frame = Rect(
            self._frame.x, self._frame.y, self._frame.width, self._frame.height - self._shrink
        )

    def setFrame_display_(self, rect: Rect, display: bool) -> None:  # noqa: N802
        self._frame = rect

    def zoom_(self, sender: object) -> None:
        self.calls.append("zoom")
        self.zoomed = not self.zoomed

    def toggleFullScreen_(self, sender: object) -> None:  # noqa: N802
        self.calls.append("fullscreen")
        self.style ^= 1 << 14

    def collectionBehavior(self) -> int:  # noqa: N802
        return self.behaviour

    def setCollectionBehavior_(self, value: int) -> None:  # noqa: N802
        self.behaviour = value

    def miniaturize_(self, sender: object) -> None:
        self.calls.append("miniaturize")

    def performWindowDragWithEvent_(self, event: object) -> None:  # noqa: N802
        self.calls.append("drag")
        self.dragged_with = event


@pytest.fixture
def on_main_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the AppKit blocks inline, against an AppKit that is not there."""

    import launchers.macoswindow as macoswindow

    monkeypatch.setattr(
        macoswindow, "on_main", lambda fn, wait=True, timeout=0.0: fn(), raising=True
    )
    monkeypatch.setattr(
        macoswindow,
        "_appkit",
        lambda: SimpleNamespace(
            NSMakeRect=lambda x, y, w, h: Rect(x, y, w, h),
            NSColor=SimpleNamespace(clearColor=lambda: "clear"),
        ),
    )


class TestInstall:
    def test_it_extends_the_content_and_hides_the_traffic_lights(
        self, on_main_here: None
    ) -> None:
        window = FakeWindow()
        MacFrame(window).install()
        assert window.transparent is True
        assert window.style & FULL_SIZE_CONTENT_VIEW
        assert all(button.hidden for button in window.buttons.values())

    def test_it_gives_back_the_height_adopting_the_mask_took(self, on_main_here: None) -> None:
        window = FakeWindow()
        MacFrame(window).install()
        assert window.frame().size.height == BEFORE.height

    # The style-mask bits that carry resizing are the ones `frameless=True`
    # clears on Windows. Here nothing but the content-view bit may be touched.
    def test_it_only_adds_the_full_size_content_view_bit(self, on_main_here: None) -> None:
        window = FakeWindow()
        before = window.style
        MacFrame(window).install()
        assert window.style == before | FULL_SIZE_CONTENT_VIEW

    # Without the collection behaviour, toggleFullScreen: is a silent no-op --
    # the green button would simply do nothing at all.
    def test_it_lets_the_window_into_full_screen(self, on_main_here: None) -> None:
        window = FakeWindow()
        MacFrame(window).install()
        assert window.behaviour & (1 << 7)


    # titlebarAppearsTransparent cannot undo a background colour set by hand, and
    # pywebview sets one on every non-frameless window. Left alone it is a 28 px
    # opaque band with the application's own top bar hidden behind it.
    def test_it_clears_the_background_pywebview_paints_on_the_title_bar(
        self, on_main_here: None
    ) -> None:
        window = FakeWindow()
        MacFrame(window).install()
        assert window.titlebar.background == "clear"
        assert [view.background for view in window.titlebar.subviews()] == ["clear"]

    # An observed view is a KVO subclass, and its class name is prefixed.
    def test_it_finds_the_title_bar_through_a_kvo_subclass(self, on_main_here: None) -> None:
        window = FakeWindow(titlebar_class=NSKVONotifying_NSTitlebarContainerView)
        MacFrame(window).install()
        assert window.titlebar.background == "clear"

    def test_it_leaves_the_rest_of_the_frame_alone(self, on_main_here: None) -> None:
        window = FakeWindow()
        MacFrame(window).install()
        others = [v for v in window._theme.subviews() if v is not window.titlebar]
        assert [view.background for view in others] == ["opaque"]


class TestActions:
    # The green button's glyph is the full-screen chevrons, so a plain press has
    # to be full screen. A glyph that promises one thing and does another is the
    # single most confusing state these three buttons can be in.
    def test_the_green_button_goes_full_screen(self, on_main_here: None) -> None:
        window = FakeWindow()
        frame = MacFrame(window)
        assert frame.toggle_fullscreen() is True
        assert window.calls == ["fullscreen"]
        assert frame.fullscreen() is True
        assert frame.toggle_fullscreen() is False

    # Option-click is zoom on the real button, and zoom is not full screen.
    def test_option_click_zooms_instead(self, on_main_here: None) -> None:
        window = FakeWindow(zoomed=False)
        frame = MacFrame(window)
        assert frame.toggle_zoom() is True
        assert window.calls == ["zoom"]
        assert frame.fullscreen() is False

    # pywebview's Cocoa `restore` is `deminiaturize_`, so a window maximized
    # through pywebview could never be un-maximized. `zoom_` is its own inverse.
    def test_zoom_restores_a_zoomed_window(self, on_main_here: None) -> None:
        window = FakeWindow(zoomed=True)
        assert MacFrame(window).toggle_zoom() is False
        assert window.zoomed is False

    def test_minimize_miniaturizes(self, on_main_here: None) -> None:
        window = FakeWindow()
        MacFrame(window).minimize()
        assert window.calls == ["miniaturize"]

    def test_a_double_click_follows_the_system_preference(
        self, on_main_here: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = FakeWindow()
        frame = MacFrame(window)
        monkeypatch.setattr(frame, "double_click_preference", lambda: "Minimize")
        assert frame.double_click() == "minimize"
        assert window.calls == ["miniaturize"]

    def test_a_double_click_set_to_none_touches_nothing(
        self, on_main_here: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = FakeWindow()
        frame = MacFrame(window)
        monkeypatch.setattr(frame, "double_click_preference", lambda: "None")
        assert frame.double_click() == "none"
        assert window.calls == []


class TestBeginDrag:
    def _frame_with_event(
        self, monkeypatch: pytest.MonkeyPatch, window: FakeWindow, event: object
    ) -> MacFrame:
        import launchers.macoswindow as macoswindow

        monkeypatch.setattr(
            macoswindow,
            "_appkit",
            lambda: SimpleNamespace(
                NSApplication=SimpleNamespace(
                    sharedApplication=lambda: SimpleNamespace(currentEvent=lambda: event)
                ),
                NSColor=SimpleNamespace(clearColor=lambda: "clear"),
            ),
        )
        return MacFrame(window)

    # The click reaches Python through the JavaScript bridge, so by then the
    # gesture may already have become its first drag. Both start a window drag.
    @pytest.mark.parametrize("event_type", sorted(DRAG_EVENT_TYPES))
    def test_a_live_gesture_is_handed_to_appkit(
        self, on_main_here: None, monkeypatch: pytest.MonkeyPatch, event_type: int
    ) -> None:
        window = FakeWindow()
        event = SimpleNamespace(type=lambda: event_type)
        self._frame_with_event(monkeypatch, window, event).begin_drag()
        assert window.dragged_with is event

    # A mouse-up is the end of a gesture, not the start of one: dragging from it
    # would move the window on a click the user has already finished.
    @pytest.mark.parametrize("event_type", [2, 5, 10])
    def test_a_finished_or_unrelated_event_starts_nothing(
        self, on_main_here: None, monkeypatch: pytest.MonkeyPatch, event_type: int
    ) -> None:
        window = FakeWindow()
        event = SimpleNamespace(type=lambda: event_type)
        self._frame_with_event(monkeypatch, window, event).begin_drag()
        assert window.calls == []

    def test_no_current_event_starts_nothing(
        self, on_main_here: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = FakeWindow()
        self._frame_with_event(monkeypatch, window, None).begin_drag()
        assert window.calls == []


class TestInstallCustomFrame:
    @pytest.mark.skipif(sys.platform == "darwin", reason="describes the other platforms")
    def test_every_other_platform_keeps_its_title_bar(self) -> None:
        assert install_custom_frame(FakeWindow()) is None

    def test_a_launcher_with_no_native_window_keeps_its_title_bar(self) -> None:
        assert install_custom_frame(None) is None

    # A window that refuses one of the calls must leave the interface drawing
    # nothing, rather than a window with no title bar and no buttons either.
    # ``on_main_here`` rather than a local ``on_main`` patch: ``install`` calls
    # ``_appkit()`` before it reaches ``setStyleMask_``, and off macOS that
    # ``import AppKit`` raises. Stubbing only ``on_main`` left the real one in
    # place, so the frame failed at the import, the assertion below never saw
    # the style mask, and the test failed on exactly the two platforms that
    # cannot have pyobjc. The fixture stubs both, which is what keeps this file
    # hermetic in the way its module docstring claims.
    def test_a_failure_anywhere_keeps_the_title_bar(
        self, monkeypatch: pytest.MonkeyPatch, on_main_here: None
    ) -> None:
        import launchers.macoswindow as macoswindow

        monkeypatch.setattr(macoswindow.sys, "platform", "darwin")
        monkeypatch.setattr(
            macoswindow, "hide_menu_bar_in_full_screen", lambda: True, raising=True
        )
        window = FakeWindow()

        failed_at_style_mask = False

        def refuse_style_mask(value: int) -> None:
            nonlocal failed_at_style_mask
            del value
            failed_at_style_mask = True
            raise RuntimeError("no")

        monkeypatch.setattr(
            window, "setStyleMask_", refuse_style_mask
        )
        assert install_custom_frame(window) is None
        assert failed_at_style_mask is True


class FakeEvent:
    """pywebview's Event, reduced to the one thing subscribing has to do."""

    def __init__(self) -> None:
        self.handlers: list[object] = []

    def __iadd__(self, handler: object) -> "FakeEvent":
        self.handlers.append(handler)
        return self


class TestArming:
    """Which event the frame is armed on, per backend.

    This is the failure the first attempt shipped: both platforms subscribed to
    ``loaded``, which the Cocoa backend fires only from
    ``webView:didFinishNavigation:`` -- and against WG's SPA that had not happened
    nine seconds in, with ``events.loaded.is_set()`` still false. The frame was
    correct and simply never installed, which looks exactly like no change at all.
    """

    def test_macos_arms_on_shown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from launchers.desktop import DesktopWindow

        monkeypatch.setattr(sys, "platform", "darwin")
        window = SimpleNamespace(events=SimpleNamespace(shown=FakeEvent(), loaded=FakeEvent()))
        DesktopWindow._arm_custom_frame(SimpleNamespace(_adopt_custom_frame="adopt"), window)
        assert window.events.shown.handlers == ["adopt"]
        assert window.events.loaded.handlers == []

    def test_windows_stays_on_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from launchers.desktop import DesktopWindow

        monkeypatch.setattr(sys, "platform", "win32")
        window = SimpleNamespace(events=SimpleNamespace(shown=FakeEvent(), loaded=FakeEvent()))
        DesktopWindow._arm_custom_frame(SimpleNamespace(_adopt_custom_frame="adopt"), window)
        assert window.events.loaded.handlers == ["adopt"]
        assert window.events.shown.handlers == []


class TestTopInset:
    """What the interface has to leave clear at the top -- nothing, either way.

    Full screen used to reserve the menu bar's height here, deliberately: the
    menu bar is not part of the window, and it slides down over whatever is
    beneath it -- the top bar, in full screen -- when the pointer reaches the
    top of the screen. Magnus reversed that trade-off on 2026-09-04: the
    always-on margin cost more than the transient overlay it bought, so this
    now returns 0 unconditionally, full screen or windowed.
    """

    def test_a_windowed_window_reserves_nothing(self, on_main_here: None) -> None:
        assert MacFrame(FakeWindow()).top_inset() == 0.0

    def test_a_full_screen_window_reserves_nothing(self, on_main_here: None) -> None:
        window = FakeWindow()
        window.style |= 1 << 14
        assert MacFrame(window).top_inset() == 0.0


class TestDeferredClose:
    """Why the close button does not close the window where it is pressed.

    `window_close` runs inside pywebview's JavaScript bridge, whose next act is
    to evaluate JavaScript in the web view to resolve the interface's promise.
    Destroying the window first takes that web view away, the
    `evaluateJavaScript:` completion handler never fires, and Cocoa's
    `evaluate_js` waits on a semaphore with no timeout -- on a thread pywebview
    does not mark as a daemon. The window closes and the process then never
    exits. Reproduced end to end, and read off the surviving thread's stack.
    """

    def _desktop(self, monkeypatch: pytest.MonkeyPatch, platform: str) -> tuple[object, list[str]]:
        from launchers.desktop import DesktopWindow

        calls: list[str] = []
        monkeypatch.setattr(sys, "platform", platform)
        desktop = SimpleNamespace(
            _window=SimpleNamespace(destroy=lambda: calls.append("destroy"))
        )
        return (lambda: DesktopWindow.window_close(desktop)), calls

    def test_macos_lets_the_bridge_finish_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import launchers.desktop as desktop_module

        close, calls = self._desktop(monkeypatch, "darwin")
        timers: list[object] = []
        monkeypatch.setattr(
            desktop_module.threading,
            "Timer",
            lambda delay, fn: SimpleNamespace(
                start=lambda: timers.append((delay, fn)) or None
            ),
        )
        close()
        assert calls == []           # nothing destroyed inside the bridge call
        assert len(timers) == 1
        delay, fn = timers[0]
        assert delay == desktop_module.BRIDGE_SETTLE_SECONDS
        fn()
        assert calls == ["destroy"]  # and the window does still close

    # Windows has no such bridge behaviour and is known good; it must not start
    # closing a quarter of a second late for a macOS problem.
    def test_windows_closes_where_it_is_pressed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        close, calls = self._desktop(monkeypatch, "win32")
        close()
        assert calls == ["destroy"]


class TestFullScreenPresentation:
    """What full screen asks AppKit for, and why it has to ask rather than tell.

    macOS's own full screen only auto-hides the menu bar: it slides back down
    whenever the pointer reaches the top of the screen, over the top bar. Setting
    the application's presentation options after DidEnterFullScreen does not
    hold -- AppKit was measured putting them back to its own 1029 within five
    seconds. The delegate is asked, so answering is the only way in.
    """

    #: FullScreen | AutoHideMenuBar | AutoHideDock -- what AppKit proposes.
    PROPOSED = (1 << 10) | PRESENTATION_AUTO_HIDE_MENU_BAR | PRESENTATION_AUTO_HIDE_DOCK

    def test_the_auto_hide_bits_become_their_unconditional_forms(self) -> None:
        answer = full_screen_presentation(self.PROPOSED)
        assert answer & PRESENTATION_HIDE_MENU_BAR
        assert answer & PRESENTATION_HIDE_DOCK
        assert not answer & PRESENTATION_AUTO_HIDE_MENU_BAR
        assert not answer & PRESENTATION_AUTO_HIDE_DOCK

    # AppKit refuses HideMenuBar on its own, so the Dock is not optional here.
    def test_the_dock_goes_with_the_menu_bar(self) -> None:
        assert full_screen_presentation(0) & PRESENTATION_HIDE_DOCK

    # Every other bit AppKit proposed is a bit it wants; building on the
    # proposal rather than replacing it is what keeps them.
    def test_it_keeps_whatever_else_appkit_asked_for(self) -> None:
        assert full_screen_presentation(self.PROPOSED) & (1 << 10)
        assert full_screen_presentation(1 << 6) & (1 << 6)

    def test_it_is_settled_after_one_pass(self) -> None:
        once = full_screen_presentation(self.PROPOSED)
        assert full_screen_presentation(once) == once
