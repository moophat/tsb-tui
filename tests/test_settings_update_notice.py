"""A late update result must be readable in Settings without reopening it."""

import asyncio
from types import SimpleNamespace

from textual.widgets import Static

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.chrome_dialogs import TextScreen, settings_lines


def test_qodo_notice_refreshes_already_open_settings():
    async def check():
        app = TsbTuiApp("http://127.0.0.1")
        async with app.run_test() as pilot:
            terminal = SimpleNamespace(dialogs=[], refresh_status=lambda: None)
            dialog = TextScreen(terminal, "Settings · the knobs in force, and where each came from",
                                lambda: settings_lines(app.cfg))
            app.push_screen(dialog)
            await pilot.pause()
            line = "tsb-tui 0.10.0 is available (you have 0.4.3). Update: curl -fsSL https://example/install.sh | bash"
            assert line not in str(dialog.query_one("#text-body", Static).content)
            app._show_update_notice(line)
            await pilot.pause()
            assert line in str(dialog.query_one("#text-body", Static).content)
            assert "update.check" in str(dialog.query_one("#text-body", Static).content)

    asyncio.run(check())
