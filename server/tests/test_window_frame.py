"""The decisions behind the custom window frame, without a window to make them on.

The ctypes plumbing in ``launchers.windowframe`` was verified against a live
WebView2 window; what is worth pinning here is the arithmetic that plumbing
carries, because each of these numbers is a distinct way for the window to look
right and behave wrong -- content sliding under the taskbar, a diagonal grip in
the wrong corner, or a grip that disappears entirely on a second monitor.

The module must also import on Linux and macOS, where ``ctypes.WINFUNCTYPE`` and
``ctypes.wintypes`` do not exist. ``launchers.desktop`` imports it unconditionally
and the suite runs on all three, so that is a real constraint rather than a
stylistic one -- and this file is what proves it, simply by importing.
"""

from __future__ import annotations

import pytest

from launchers.windowframe import (
    CORNER_GRIP,
    HTCAPTION,
    HTCLIENT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    WindowBounds,
    caption_free_top,
    install_custom_frame,
    resize_hit,
    signed_word,
)

HTBOTTOMRIGHT = 17
HTLEFT = 10

#: A 900x600 window at (100, 100), and the 8 px frame Windows reports on a
#: 100 % display -- the numbers the live probe measured.
BOUNDS = WindowBounds(left=100, top=100, right=1000, bottom=700)
BORDER = 8


class TestCaptionFreeTop:
    def test_a_restored_window_gives_the_whole_caption_to_the_app(self) -> None:
        assert caption_free_top(100, maximized=False, border=BORDER) == 100

    # A maximized window's rect overhangs the work area by exactly the frame
    # thickness. Following it would put the top bar -- and the close button in
    # it -- above the top of the screen, where it cannot be clicked.
    def test_a_maximized_window_keeps_the_frame_it_overhangs_by(self) -> None:
        assert caption_free_top(-8, maximized=True, border=BORDER) == 0

    def test_the_inset_tracks_the_reported_border(self) -> None:
        assert caption_free_top(0, maximized=True, border=11) == 11


class TestResizeHit:
    def test_the_top_edge_becomes_a_resize_grip(self) -> None:
        assert resize_hit(
            HTCAPTION, x=550, y=102, bounds=BOUNDS, border=BORDER, maximized=False
        ) == HTTOP

    def test_below_the_grip_stays_draggable(self) -> None:
        assert resize_hit(
            HTCAPTION, x=550, y=120, bounds=BOUNDS, border=BORDER, maximized=False
        ) == HTCAPTION

    @pytest.mark.parametrize(
        ("x", "expected"),
        [
            (100, HTTOPLEFT),
            (100 + CORNER_GRIP - 1, HTTOPLEFT),
            (100 + CORNER_GRIP, HTTOP),
            (1000 - CORNER_GRIP - 1, HTTOP),
            # `right` is exclusive, so this pair is what keeps both corner
            # grips exactly CORNER_GRIP pixels wide.
            (1000 - CORNER_GRIP, HTTOPRIGHT),
            (999, HTTOPRIGHT),
        ],
    )
    def test_the_corners_take_the_diagonal_grip(self, x: int, expected: int) -> None:
        assert resize_hit(
            HTCAPTION, x=x, y=101, bounds=BOUNDS, border=BORDER, maximized=False
        ) == expected

    # The client area is where the interface lives; upgrading a hit there would
    # make the top of the top bar un-clickable rather than resizable.
    def test_a_client_hit_below_the_band_is_left_alone(self) -> None:
        assert resize_hit(
            HTCLIENT, x=550, y=400, bounds=BOUNDS, border=BORDER, maximized=False
        ) == HTCLIENT

    # Windows already answered for its own frame on the other three edges. Those
    # answers are the correct ones and must survive untouched.
    @pytest.mark.parametrize("default", [HTLEFT, HTBOTTOMRIGHT])
    def test_the_frame_windows_still_owns_is_never_overridden(self, default: int) -> None:
        assert resize_hit(
            default, x=100, y=101, bounds=BOUNDS, border=BORDER, maximized=False
        ) == default

    # A maximized window has no edge to grab, and a grip there would un-maximize
    # on a mis-click at the top of the screen -- exactly where the pointer is
    # thrown to reach the close button.
    def test_a_maximized_window_offers_no_grip(self) -> None:
        assert resize_hit(
            HTCAPTION, x=550, y=101, bounds=BOUNDS, border=BORDER, maximized=True
        ) == HTCAPTION

    # A window on a monitor left of the primary has negative screen coordinates.
    # Reading them unsigned puts the pointer near x=65000, which is outside every
    # corner test and silently loses the grip on exactly one monitor.
    def test_the_grip_survives_a_monitor_left_of_the_primary(self) -> None:
        bounds = WindowBounds(left=-1820, top=-100, right=-920, bottom=500)
        assert resize_hit(
            HTCAPTION, x=-1816, y=-99, bounds=bounds, border=BORDER, maximized=False
        ) == HTTOPLEFT


class TestSignedWord:
    @pytest.mark.parametrize(
        ("packed", "expected"),
        [(0, 0), (100, 100), (0x7FFF, 32767), (0x8000, -32768), (0xFFFF, -1)],
    )
    def test_reads_a_packed_coordinate_as_signed(self, packed: int, expected: int) -> None:
        assert signed_word(packed) == expected

    def test_ignores_the_other_half_of_the_lparam(self) -> None:
        lparam = (200 & 0xFFFF) << 16 | (150 & 0xFFFF)
        assert signed_word(lparam) == 150
        assert signed_word(lparam >> 16) == 200


class TestInstallCustomFrame:
    # The window buttons are drawn on this returning something. Off Windows it
    # must return None rather than raise, so the desktop window still opens --
    # with its own OS title bar -- on a platform that has no caption to take.
    def test_declines_quietly_off_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("launchers.windowframe.sys.platform", "darwin")
        assert install_custom_frame(12345) is None

    def test_declines_rather_than_raising_on_a_handle_that_is_not_a_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("launchers.windowframe.sys.platform", "win32")
        monkeypatch.setattr(
            "launchers.windowframe._user32",
            lambda: (_ for _ in ()).throw(OSError("no such window")),
        )
        assert install_custom_frame(0) is None
