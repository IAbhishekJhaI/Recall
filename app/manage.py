"""Command-line account management.

    python -m app.manage create-user alice [--admin]
    python -m app.manage reset-password alice
    python -m app.manage list-users
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .accounts import AccountStore
from .config import Settings


def _password() -> str:
    while True:
        pw = getpass.getpass("Password (min 8 chars): ")
        if len(pw) < 8:
            print("Too short.")
            continue
        if getpass.getpass("Repeat password: ") != pw:
            print("Passwords don't match.")
            continue
        return pw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("--admin", action="store_true")
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    sub.add_parser("list-users")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    accounts = AccountStore(settings.accounts_db, settings.session_days)

    if args.cmd == "create-user":
        try:
            u = accounts.create_user(args.username, _password(), args.admin)
        except ValueError as e:
            print(e)
            return 1
        print(f"Created {'admin ' if u.is_admin else ''}user {u.username}")
    elif args.cmd == "reset-password":
        match = [u for u in accounts.list_users() if u.username.lower() == args.username.lower()]
        if not match:
            print("No such user")
            return 1
        accounts.update_user(match[0].id, password=_password())
        print("Password updated; existing sessions signed out.")
    elif args.cmd == "list-users":
        for u in accounts.list_users():
            flags = ", ".join(f for f, on in (("admin", u.is_admin), ("disabled", u.disabled)) if on)
            print(f"{u.username}{f'  ({flags})' if flags else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
