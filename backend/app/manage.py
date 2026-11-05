"""Command line administration (e.g. when nobody can log in any more).

    python -m app.manage create-admin <username>
    python -m app.manage reset-password <username>

In Docker:  docker compose exec backend python -m app.manage create-admin chef
The password is asked interactively and must be changed at the next login.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .auth import check_password_policy, end_sessions_of, hash_password
from .db import User, audit, get_engine


def _ask_password(username: str) -> str:
    pw = getpass.getpass("Neues Passwort: ")
    if pw != getpass.getpass("Wiederholen: "):
        sys.exit("Die Passwörter stimmen nicht überein.")
    try:
        check_password_policy(pw, username)
    except HTTPException as exc:
        sys.exit(str(exc.detail))
    return pw


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-admin").add_argument("username")
    sub.add_parser("reset-password").add_argument("username")
    args = parser.parse_args(argv)

    with sessionmaker(get_engine())() as session:
        user = session.scalar(select(User).where(User.username == args.username))
        if args.cmd == "create-admin":
            if user is not None:
                sys.exit(f"Benutzer '{args.username}' existiert bereits.")
            session.add(
                User(
                    username=args.username,
                    password_hash=hash_password(_ask_password(args.username)),
                    role="admin",
                    must_change_password=True,
                )
            )
            audit(session, "cli", "user_created", username=args.username, role="admin")
        else:
            if user is None:
                sys.exit(f"Benutzer '{args.username}' gibt es nicht.")
            user.password_hash = hash_password(_ask_password(args.username))
            user.must_change_password = True
            user.active = True
            end_sessions_of(session, user.id)
            audit(session, "cli", "password_reset", username=args.username)
        session.commit()
    print("OK – das Passwort muss bei der nächsten Anmeldung geändert werden.")


if __name__ == "__main__":
    main()
