"""`tsb-tui` — `python -m tsb_tui` or the installed script (`[project.scripts]`, pyproject.toml).

Interactive: `tsb-tui --broker URL [--ca-file PEM] [--target ID]`.
Non-interactive smoke path, exit 0/1 (the same binary the deploy hook runs after a deploy):
`tsb-tui --smoke --broker URL --target ID [--ca-file PEM]`.
"""

from __future__ import annotations

import argparse
import sys

# Imported eagerly, not lazily like `smoke`/`app` below: a broken install — the contract client
# missing from this venv — then fails right here, at `--help`, instead of only at the first real
# call (argparse's own `--help` handling returns before either lazy import below ever runs).
import tsb_contract_client  # noqa: F401


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tsb-tui")
    parser.add_argument("--broker", required=True, help="the core's base URL, e.g. https://host")
    parser.add_argument("--ca-file", default=None, help="a PEM bundle to verify the core's certificate against")
    parser.add_argument("--target", default=None, help="a target id (no target list at this tag)")
    parser.add_argument("--smoke", action="store_true",
                        help="non-interactive: login, create, attach, one round trip, close, exit 0/1")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.smoke:
        from . import smoke

        if not args.target:
            print("tsb-tui --smoke: --target is required", file=sys.stderr)
            return 1
        return smoke.run(args.broker, args.target, args.ca_file)

    from .app import TsbTuiApp

    try:
        # A cleartext broker URL is refused here (`_http.HttpClient`) unless the host is
        # loopback — otherwise the login password would cross the network in clear.
        app = TsbTuiApp(args.broker, ca_file=args.ca_file, target=args.target)
    except ValueError as exc:
        print(f"tsb-tui: {exc}", file=sys.stderr)
        return 1
    app.run()
    # A crashed worker (an attach or an API call that raised outside its own handling) sets this
    # non-zero; the shell that ran `tsb-tui` should see that, not a plain 0 every time.
    return app.return_code or 0


if __name__ == "__main__":
    sys.exit(main())
