"""Command line entry point: ``fds-mcp``.

Subcommands:
    serve       run the MCP server over stdio (the default)
    configure   store the OAuth application data (client id, redirect URI, scopes)
    login       run the Authorization-Code + PKCE flow and store the tokens
    whoami      show the authenticated account
    status      show token, configuration and throttle state
    logout      delete the stored tokens (and optionally revoke them)
    validate    run the rule set against a draft file, without the MCP layer
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config, drafts, rules
from .auth import (
    AuthError,
    ClientConfig,
    load_client_config,
    load_tokens,
    revoke,
    save_client_config,
)
from .auth import (
    login as do_login,
)
from .client import FdsClient
from .config import DEFAULT_REDIRECT_URI, DEFAULT_SCOPES
from .throttle import ThrottleLedger


def _cmd_serve(_args: argparse.Namespace) -> int:
    from .server import run

    run()
    return 0


def _cmd_configure(args: argparse.Namespace) -> int:
    cfg = ClientConfig(
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        scopes=tuple(args.scopes.split()) if args.scopes else DEFAULT_SCOPES,
    )
    path = save_client_config(cfg)
    print(f"Saved to {path} (mode 0600).")
    print(f"client_id: {cfg.client_id}")
    print(f"redirect_uri: {cfg.redirect_uri}")
    print(f"scopes: {' '.join(cfg.scopes)}")
    if cfg.client_secret:
        print("client_secret: stored (not shown)")
    print("\nNext: fds-mcp login")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    do_login(manual=args.manual, open_browser=not args.no_browser, port=args.port)
    return 0


def _cmd_whoami(_args: argparse.Namespace) -> int:
    with FdsClient.authenticated() as client:
        me = client.whoami()
    print(f"id: {me.get('id')}")
    print(f"name: {me.get('first_name', '')} {me.get('last_name', '')}".strip())
    print(f"username: {me.get('username')}")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    print(f"fds-mcp {__version__}")
    print(f"config dir: {config.home()}")
    try:
        cfg = load_client_config()
        print(f"client_id:  {cfg.client_id}")
        print(f"redirect:   {cfg.redirect_uri}")
        print(f"scopes:     {' '.join(cfg.scopes)}")
    except AuthError as exc:
        print(f"client:     NOT CONFIGURED — {exc}")
    tokens = load_tokens()
    if tokens is None:
        print("tokens:     none stored (run: fds-mcp login)")
    else:
        state = "expired" if tokens.expired else "valid"
        print(f"tokens:     {state}, scopes: {tokens.scope or '(unknown)'}")
        print(f"            file {config.tokens_path()}")
    ledger = ThrottleLedger()
    status = ledger.status()
    print("throttle:   " + ", ".join(
        f"{w['used']}/{w['limit']} per {w['window']}" for w in status["windows"]))
    for problem in status["would_block"]:
        print(f"            BLOCKED: {problem}")
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    tokens = load_tokens()
    if tokens is None:
        print("No tokens stored.")
        return 0
    if args.revoke:
        try:
            revoke(load_client_config(), tokens)
            print("Access token revoked at fragdenstaat.de.")
        except AuthError as exc:
            print(f"Revocation failed: {exc}", file=sys.stderr)
    from .auth import forget_tokens

    forget_tokens()
    print(f"Deleted {config.tokens_path()}.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    draft = drafts.load(args.path)
    findings = rules.run_offline(draft)
    if args.live:
        with FdsClient() as client:
            findings += rules.run_live(draft, client)
    order = ["ERROR", "WARN", "INFO"]
    for finding in sorted(findings, key=lambda f: order.index(f.level.value)):
        print(finding)
    errs = rules.errors(findings)
    print(f"\n{len(errs)} error(s), {len(findings) - len(errs)} hint(s).")
    return 1 if errs else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fds-mcp",
        description="MCP server and CLI for the FragDenStaat.de (froide) API.",
    )
    parser.add_argument("--version", action="version", version=f"fds-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="run the MCP server over stdio")
    p_serve.set_defaults(func=_cmd_serve)

    p_cfg = sub.add_parser(
        "configure",
        help="store the OAuth application data",
        epilog=f"Register an application first at {config.register_application_url()} "
               "(client type 'public', grant type 'authorization-code').",
    )
    p_cfg.add_argument("--client-id", required=True)
    p_cfg.add_argument("--client-secret", default=None,
                       help="only for confidential clients; public clients use PKCE")
    p_cfg.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI,
                       help=f"default: {DEFAULT_REDIRECT_URI} "
                            "(only https:// and fragdenstaat:// are accepted)")
    p_cfg.add_argument("--scopes", default=" ".join(DEFAULT_SCOPES))
    p_cfg.set_defaults(func=_cmd_configure)

    p_login = sub.add_parser("login", help="OAuth2 Authorization Code + PKCE")
    p_login.add_argument("--manual", action="store_true",
                         help="use fragdenstaat://callback and paste the URL back")
    p_login.add_argument("--no-browser", action="store_true")
    p_login.add_argument("--port", type=int, default=config.DEFAULT_CALLBACK_PORT)
    p_login.set_defaults(func=_cmd_login)

    p_who = sub.add_parser("whoami", help="show the authenticated account")
    p_who.set_defaults(func=_cmd_whoami)

    p_status = sub.add_parser("status", help="config, token and throttle state")
    p_status.set_defaults(func=_cmd_status)

    p_logout = sub.add_parser("logout", help="delete the stored tokens")
    p_logout.add_argument("--revoke", action="store_true",
                          help="also revoke the token at fragdenstaat.de")
    p_logout.set_defaults(func=_cmd_logout)

    p_val = sub.add_parser("validate", help="run the rule set against a draft file")
    p_val.add_argument("path", type=Path)
    p_val.add_argument("--live", action="store_true", help="also run L01-L05 (network)")
    p_val.set_defaults(func=_cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # No subcommand: behave like an MCP server, which is how clients launch us.
        return _cmd_serve(args)
    try:
        return int(args.func(args) or 0)
    except (AuthError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
