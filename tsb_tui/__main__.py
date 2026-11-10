"""`tsb-tui` — `python -m tsb_tui` or the installed script (`[project.scripts]`, pyproject.toml).

Interactive: `tsb-tui [--broker URL] [--ca-file PEM] [--target ID] [--profile NAME] [--config FILE]
[--set KEY=VALUE ...]`.
Non-interactive smoke path, exit 0/1 (2 when no broker is configured) (the same binary the deploy hook runs after a deploy):
`tsb-tui --smoke [--broker URL] --target ID [--ca-file PEM]`.

Agents and the harness: `tsb-tui drive --broker URL --user U --password-file PATH` runs the same app headless and
speaks JSON lines (`tsb-tui drive --guide` prints what an agent is told; `tsb-tui drive --help` the flags).

Knobs come from the packaged defaults, a profile, the launcher-selected or OS config file, `TSB_TUI_*` and
`--set`, in that order of precedence (design D30). A start-up that cannot proceed — an unknown key,
a bad type, an engine that cannot load — exits 1 naming the key or the path.
"""

from __future__ import annotations

import argparse
import sys

# Imported eagerly, not lazily like `smoke`/`app` below: a broken install — the contract client
# missing from this venv — then fails right here, at `--help`, instead of only at the first real
# call (argparse's own `--help` handling returns before either lazy import below ever runs).
import tsb_contract_client  # noqa: F401

from . import config
from .screens.chrome_dialogs import tui_version


def _config_help() -> str:
    """Short, platform-correct description of the default config file location."""
    if sys.platform == "win32":
        return (
            "a TOML configuration file overriding the file beside the program (TSB_TUI_HOME), "
            "instead of %%APPDATA%%\\tsb-tui\\config.toml "
            "(or $XDG_CONFIG_HOME/tsb-tui/config.toml when XDG_CONFIG_HOME is set)"
        )
    return (
        "a TOML configuration file overriding the file beside the program (TSB_TUI_HOME), "
        "instead of $XDG_CONFIG_HOME/tsb-tui/config.toml "
        "(defaults to ~/.config/tsb-tui/config.toml)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsb-tui", epilog="`tsb-tui drive --help` is the agent surface: the same app, headless, as JSON lines.")
    parser.add_argument("--version", action="version", version=tui_version())
    parser.add_argument("--broker", default=None, help="the core's base URL (default: the broker.url knob)")
    parser.add_argument("--ca-file", default=None, help="a PEM bundle to verify the core's certificate against")
    parser.add_argument("--target", default=None, help="a target id")
    parser.add_argument("--smoke", action="store_true",
                        help="non-interactive: load the engine, login, create, attach, one round trip, "
                             "close, exit 0/1")
    parser.add_argument("--profile", default=None, choices=config.PROFILES,
                        help="a configuration profile (default, phone, drive, harness)")
    parser.add_argument("--config", default=None, metavar="FILE",
                        help=_config_help())
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="set one knob, e.g. --set size.auto_request=true (repeatable; wins over everything)")
    return parser


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _load_config(args: argparse.Namespace, *, for_smoke: bool) -> config.Config:
    overrides = list(args.overrides)
    if for_smoke:
        # The smoke path sends no size request and paints nothing, so auto size means nothing to it.
        # Put first, so an explicit --set still wins.
        overrides.insert(0, "size.auto_request=false")
    return config.load(profile=args.profile, config_file=args.config, overrides=overrides)


def _missing_broker(config_file: str | None) -> int:
    import os
    from pathlib import Path

    path = Path(config_file) if config_file is not None else config.default_config_path(os.environ)
    print(f"tsb-tui: no broker address. Pass --broker URL, set broker.url in {path}, "
          "or set TSB_TUI_BROKER_URL.", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["drive"]:
        # A subcommand with its own flags; --guide and --help need no broker.
        from .drive import runner

        return runner.main(argv[1:])
    args = _parse_args(argv)
    from . import app as app_mod
    from .theme import ThemeError

    if args.smoke:
        from . import smoke

        if not args.target:
            print("tsb-tui --smoke: --target is required", file=sys.stderr)
            return 1
        try:
            cfg = _load_config(args, for_smoke=True)
        except config.ConfigError as exc:
            print(f"tsb-tui: {exc}", file=sys.stderr)
            return 1
        broker = config.resolve_broker(args.broker, cfg)
        if broker is None:
            return _missing_broker(args.config)
        source = None if args.broker else cfg.source("broker.url")
        return smoke.run(broker, args.target, args.ca_file, cfg=cfg, broker_source=source)

    try:
        cfg = _load_config(args, for_smoke=False)
        broker = config.resolve_broker(args.broker, cfg)
        if broker is None:
            return _missing_broker(args.config)
        # A cleartext broker URL is refused here (`_http.HttpClient`) unless the host is
        # loopback — otherwise the login password would cross the network in clear.
        try:
            app = app_mod.TsbTuiApp(broker, ca_file=args.ca_file, target=args.target, cfg=cfg)
        except ValueError as exc:
            if not args.broker:
                raise ValueError(f"broker.url ({cfg.source('broker.url')}): {exc}") from None
            raise
    except (config.ConfigError, app_mod.EngineError, ThemeError, ValueError) as exc:
        print(f"tsb-tui: {exc}", file=sys.stderr)
        return 1
    app.run()
    # A crashed worker (an attach or an API call that raised outside its own handling) sets this
    # non-zero; the shell that ran `tsb-tui` should see that, not a plain 0 every time.
    return app.return_code or 0


if __name__ == "__main__":
    sys.exit(main())
