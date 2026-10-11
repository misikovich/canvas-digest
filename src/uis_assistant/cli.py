"""`uis` command line: init, sync, tick, notify, digest, status, mcp."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from uis_assistant import notify, queries
from uis_assistant.canvas.client import CanvasClient, CanvasError
from uis_assistant.config import CONFIG_TEMPLATE, Config, ConfigError, load_config
from uis_assistant.runner import run_sync
from uis_assistant.store import Store


def _setup_logging(cfg: Config, verbose: bool) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(cfg.data_dir / "uis.log", maxBytes=1_000_000, backupCount=3)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    # stdout of `tick` is the Telegram message, so stderr stays quiet unless asked
    if verbose:
        root.addHandler(logging.StreamHandler(sys.stderr))
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_init(cfg: Config, args) -> int:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    if not cfg.config_file.exists():
        cfg.config_file.write_text(CONFIG_TEMPLATE)
        print(f"Wrote {cfg.config_file}")

    if args.token_stdin:
        token = sys.stdin.readline().strip()
    elif args.new_token or not cfg.token_file.exists():
        print("Create a token in Canvas: Account → Settings → + New Access Token")
        token = getpass.getpass("Canvas access token: ").strip()
    else:
        token = None
    if token:
        fd = os.open(cfg.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        os.chmod(cfg.token_file, 0o600)
        print(f"Saved token to {cfg.token_file} (mode 600)")

    try:
        with CanvasClient(cfg.base_url, cfg.token()) as client:
            me = client.get_json("/api/v1/users/self")
        print(f"Token works: logged in as {me.get('name')}")
    except (CanvasError, ConfigError) as e:
        print(f"Token check failed: {e}", file=sys.stderr)
        return 1

    return cmd_sync(cfg, args)


def cmd_sync(cfg: Config, args) -> int:
    store = Store(cfg.db_path)
    try:
        outcome = run_sync(cfg, store)
        print(outcome.summary())
        if outcome.ok:
            print(queries.status(store, cfg))
        return 0 if outcome.ok or outcome.skipped else 1
    finally:
        store.close()


def cmd_tick(cfg: Config, args) -> int:
    """Sync, then print the notification. Sync failures surface as health alerts, exit 0."""
    store = Store(cfg.db_path)
    try:
        outcome = run_sync(cfg, store)
        logging.getLogger("uis.tick").info(outcome.summary())
        return _notify(store, cfg, dry_run=False)
    finally:
        store.close()


def cmd_notify(cfg: Config, args) -> int:
    store = Store(cfg.db_path)
    try:
        return _notify(store, cfg, dry_run=args.dry_run)
    finally:
        store.close()


def _notify(store: Store, cfg: Config, dry_run: bool) -> int:
    n = notify.build(store, cfg)
    print(n.text)
    if not dry_run:
        n.commit()
    return 0


def cmd_digest(cfg: Config, args) -> int:
    store = Store(cfg.db_path)
    try:
        print(queries.digest(store, cfg, "week" if args.week else "day"))
    finally:
        store.close()
    return 0


def cmd_status(cfg: Config, args) -> int:
    store = Store(cfg.db_path)
    try:
        print(queries.status(store, cfg))
    finally:
        store.close()
    return 0


def cmd_mcp(cfg: Config, args) -> int:
    from uis_assistant.mcp_server import build_server

    build_server(cfg).run(transport="stdio")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="uis", description="UiS Canvas assistant")
    p.add_argument("-v", "--verbose", action="store_true", help="log to stderr")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write config, store the Canvas token, run a first sync")
    s.add_argument("--new-token", action="store_true", help="replace the saved token")
    s.add_argument("--token-stdin", action="store_true", help="read the token from stdin")
    s.set_defaults(fn=cmd_init)
    sub.add_parser("sync", help="fetch Canvas and update the store").set_defaults(fn=cmd_sync)
    sub.add_parser("tick", help="sync + notify; prints the Telegram message or NO_REPLY").set_defaults(fn=cmd_tick)
    s = sub.add_parser("notify", help="print pending notifications")
    s.add_argument("--dry-run", action="store_true", help="don't mark anything as sent")
    s.set_defaults(fn=cmd_notify)
    s = sub.add_parser("digest", help="print the daily (or weekly) digest")
    s.add_argument("--week", action="store_true")
    s.set_defaults(fn=cmd_digest)
    sub.add_parser("status", help="sync health and item counts").set_defaults(fn=cmd_status)
    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(fn=cmd_mcp)

    args = p.parse_args(argv)
    cfg = load_config()
    _setup_logging(cfg, args.verbose)
    return args.fn(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
