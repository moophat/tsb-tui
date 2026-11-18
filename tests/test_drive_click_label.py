"""Label clicks resolve coordinates after pending layout work (P1 Amendment 5)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tsb_tui.drive.runner import Driver


@pytest.mark.parametrize("refresh_at", [1, 2])
def test_click_label_follows_a_label_moved_by_pending_refresh(refresh_at) -> None:
    async def check() -> None:
        label_x = 2
        clicked = []
        def forward(event) -> None:
            pass
        screen = SimpleNamespace(
            _forward_event=forward,
            _compositor=SimpleNamespace(
                render_strips=lambda: [SimpleNamespace(text=" " * label_x + "Menu")]),
            get_widget_at=lambda x, y: (object(), None),
        )

        refreshes = 0
        async def finish_refresh() -> None:
            nonlocal label_x, refreshes
            refreshes += 1
            if refreshes == refresh_at:
                label_x = 20

        async def click(*, offset) -> None:
            clicked.append(offset)

        driver = Driver.__new__(Driver)
        driver.app = SimpleNamespace(screen=screen)
        driver.pilot = SimpleNamespace(pause=finish_refresh, click=click)
        driver.screen_lock = SimpleNamespace(check=lambda: False)

        assert await driver.click_label("Menu") == {}
        assert clicked == [(22, 0)], "the click must use the label's settled position"
        assert len(clicked) == 1, "the settled label is clicked exactly once"
        assert screen._forward_event is forward

    asyncio.run(check())


def test_click_label_action_that_moves_label_fires_once() -> None:
    async def check() -> None:
        label_x = 2
        clicks = []
        def forward(event) -> None:
            pass
        screen = SimpleNamespace(
            _forward_event=forward,
            _compositor=SimpleNamespace(
                render_strips=lambda: [SimpleNamespace(text=" " * label_x + "Menu")]),
            get_widget_at=lambda x, y: (object(), None),
        )
        async def refresh() -> None:
            pass
        async def click(*, offset) -> None:
            nonlocal label_x
            clicks.append(offset)
            label_x = 20
        driver = Driver.__new__(Driver)
        driver.app = SimpleNamespace(screen=screen)
        driver.pilot = SimpleNamespace(pause=refresh, click=click)
        driver.screen_lock = SimpleNamespace(check=lambda: False)
        assert await driver.click_label("Menu") == {}
        assert clicks == [(4, 0)], "the action must not run again after moving its label"
        assert screen._forward_event is forward
    asyncio.run(check())
