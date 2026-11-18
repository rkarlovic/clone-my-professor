r"""Register an LMS (LTI 1.3 platform) with CMP, or list the registered ones.

    uv run python -m scripts.lti_platform config          # the URLs to enter in the LMS
    uv run python -m scripts.lti_platform list
    uv run python -m scripts.lti_platform ws --issuer http://127.0.0.1:8080 \
        --base-url http://127.0.0.1:8080 --token-env MOODLE_WS_TOKEN   # material sync
    uv run python -m scripts.lti_platform add --name "Moodle (dev)" \
        --issuer http://127.0.0.1:8080 --client-id <from the LMS> \
        --auth-url http://127.0.0.1:8080/mod/lti/auth.php \
        --jwks-url http://127.0.0.1:8080/mod/lti/certs.php [--deployment-id 1]
    # Teams / Google Classroom (launch by LTI 1.3 as above; material sync through their APIs):
    uv run python -m scripts.lti_platform sync --issuer <issuer> --kind teams \
        --tenant-id <Entra tenant id> --client-id <app id> --secret-env CMP_TEAMS_SECRET
    uv run python -m scripts.lti_platform sync --issuer <issuer> --kind classroom \
        --subject sync@school.edu --secret-env CMP_GOOGLE_SA_KEY   # key JSON or its path

For Moodle the values are on the tool's "View configuration details" page. The admin page
(#/admin) does the same in the browser.
"""

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine
from core.lti import tool_urls
from core.models import LtiPlatform


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config")
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--issuer", required=True)
    add.add_argument("--client-id", required=True)
    add.add_argument("--auth-url", required=True)
    add.add_argument("--jwks-url", required=True)
    add.add_argument("--deployment-id", action="append", default=[])
    ws = sub.add_parser("ws", help="set the Web Services address and the env var with the token")
    ws.add_argument("--issuer", required=True)
    ws.add_argument("--base-url", required=True)
    ws.add_argument("--token-env", required=True, help="NAME of the variable, never the token")
    sync = sub.add_parser("sync", help="material sync for a Teams or Google Classroom platform")
    sync.add_argument("--issuer", required=True)
    sync.add_argument("--kind", choices=["teams", "classroom"], required=True)
    sync.add_argument("--tenant-id")
    sync.add_argument("--client-id", help="Teams: the app (client) id that reads files")
    sync.add_argument("--subject", help="Classroom: the Workspace user the account acts as")
    sync.add_argument("--secret-env", required=True, help="NAME of the variable with the secret")
    args = ap.parse_args()

    if args.cmd == "config":
        print(json.dumps(tool_urls(), indent=2))
        return
    with Session(app_engine()) as s, s.begin():
        if args.cmd == "ws":
            for p in s.scalars(select(LtiPlatform).where(LtiPlatform.issuer == args.issuer)):
                p.ws_base_url, p.ws_token_env = args.base_url, args.token_env
                print(f"{p.name}: sync via {args.base_url}, token from ${args.token_env}")
            return
        if args.cmd == "sync":
            cfg = (
                {"tenant_id": args.tenant_id, "client_id": args.client_id}
                if args.kind == "teams"
                else {"subject": args.subject}
            )
            if not all(cfg.values()):
                raise SystemExit(
                    f"{args.kind} needs: {', '.join(k for k, v in cfg.items() if not v)}"
                )
            for p in s.scalars(select(LtiPlatform).where(LtiPlatform.issuer == args.issuer)):
                p.kind, p.sync_config, p.ws_token_env = args.kind, cfg, args.secret_env
                print(f"{p.name}: {args.kind} sync, secret from ${args.secret_env}")
            return
        if args.cmd == "list":
            for p in s.scalars(select(LtiPlatform)):
                print(
                    f"{p.name} ({p.kind}): {p.issuer} client {p.client_id} "
                    f"deployments {p.deployment_ids}"
                )
            return
        existing = s.scalars(
            select(LtiPlatform).where(
                LtiPlatform.issuer == args.issuer, LtiPlatform.client_id == args.client_id
            )
        ).first()
        p = existing or LtiPlatform(issuer=args.issuer, client_id=args.client_id)
        p.name, p.auth_login_url, p.jwks_url = args.name, args.auth_url, args.jwks_url
        p.deployment_ids = args.deployment_id
        s.add(p)
    print(("updated" if existing else "registered") + f": {args.name}")


if __name__ == "__main__":
    main()
