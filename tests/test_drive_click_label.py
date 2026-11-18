"""Label clicks resolve coordinates after pending layout work (P1 Amendment 5)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tsb_tui.drive.runner import Driver


def test_click_label_follows_a_label_moved_by_pending_refresh() -> None:
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

        async def finish_refresh() -> None:
            pass

        async def click(*, offset) -> None:
            # Pilot drains pending screen work before dispatching its mouse event.
            nonlocal label_x
            label_x = 20
            clicked.append(offset == (label_x + 2, 0))

        driver = Driver.__new__(Driver)
        driver.app = SimpleNamespace(screen=screen)
        driver.pilot = SimpleNamespace(pause=finish_refresh, click=click)
        driver.screen_lock = SimpleNamespace(check=lambda: False)

        assert await driver.click_label("Menu") == {}
        assert clicked[-1] is True, "the click used the label's position before refresh"
        assert len(clicked) <= 2, "a moved label is retried at most once"
        assert screen._forward_event is forward

    asyncio.run(check())
