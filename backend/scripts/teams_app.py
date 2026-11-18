"""Build the Microsoft Teams app package (a .zip the school's Teams admin uploads) for the CMP tab.

    uv run python -m scripts.teams_app [--out cmp-teams.zip] [--developer "Škola XY"]
        [--privacy-url https://…] [--terms-url https://…]
    docker compose ... exec api python -m scripts.teams_app --out /data/cmp-teams.zip

Needs PUBLIC_URL (https, the address Teams loads CMP from) and TEAMS_APP_CLIENT_ID (the Entra
app Teams single sign-on issues tokens for); TEAMS_APP_ID_URI defaults to
api://<host>/<client id>, which must match "Application ID URI" in the app registration.

What the school's IT does once (the admin page lists it too):
  1. Entra ID app registration (or reuse the one for material sync):
     - Expose an API: Application ID URI api://<host>/<client id>, scope "access_as_user",
       pre-authorise Teams' clients 1fec8e78-bce4-4aaf-ab1b-5451cc387264 (desktop/mobile) and
       5e3ce6c0-2b1f-4285-8d4b-75ee78787346 (web)
     - Application permissions with admin consent: GroupMember.Read.All (who owns / is in a
       team) and Files.Read.All or Sites.Selected (material sync); a client secret in .env
     - Manifest: "accessTokenAcceptedVersion": 2
  2. Register the tenant in CMP (admin page, kind "Microsoft Teams", or scripts.lti_platform)
  3. Upload this package in the Teams admin centre and allow it for teachers
  4. In .env: CMP_FRAME_ANCESTORS must include Teams (printed below), so the tab may embed CMP

The package id stays the same for an installation (derived from PUBLIC_URL), so a new build
replaces the old app instead of adding a second one.
"""

import argparse
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from core.settings import get_settings

TEAMS_FRAME_ANCESTORS = (
    "https://teams.microsoft.com https://*.teams.microsoft.com https://*.cloud.microsoft "
    "https://*.office.com https://*.skype.com"
)
ACCENT = "#2F5DA8"


def _icon(size: int, outline: bool) -> bytes:
    """A simple icon: the letters CMP (Pillow comes with the ingest group)."""
    from PIL import Image, ImageDraw, ImageFont

    bg = (0, 0, 0, 0) if outline else (47, 93, 168, 255)
    img = Image.new("RGBA", (size, size), bg)
    draw = ImageDraw.Draw(img)
    if outline:
        draw.rounded_rectangle(
            [1, 1, size - 2, size - 2], radius=size // 6, outline=(255, 255, 255, 255), width=2
        )
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size // 3)
    except OSError:
        font = ImageFont.load_default()
    text = "C" if outline else "CMP"
    box = draw.textbbox((0, 0), text, font=font)
    pos = ((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1])
    draw.text(pos, text, fill=(255, 255, 255, 255), font=font)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def manifest(
    public_url: str, client_id: str, id_uri: str, developer: str, privacy: str, terms: str
) -> dict:
    host = urlsplit(public_url).netloc
    return {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
        "manifestVersion": "1.17",
        "version": "1.0.0",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, public_url + "/teams-app")),
        "developer": {
            "name": developer,
            "websiteUrl": public_url,
            "privacyUrl": privacy,
            "termsOfUseUrl": terms,
        },
        "name": {"short": "Asistent (CMP)", "full": "Clone My Professor: AI asistent kolegija"},
        "description": {
            "short": "AI asistent koji odgovara samo iz materijala nastavnika.",
            "full": (
                "Asistent odgovara studentima samo iz materijala koje je nastavnik odobrio "
                "i uvijek navodi izvor (datoteku i slajd). Na pitanja na koja ne zna odgovor, "
                "kao i na pitanja o ocjenama i rokovima, ne odgovara nego ih prosljeđuje "
                "nastavniku. Studenti uvijek znaju da razgovaraju s AI asistentom."
            ),
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": ACCENT,
        "configurableTabs": [
            {
                "configurationUrl": f"{public_url}/#/teams/config",
                "canUpdateConfiguration": False,
                "scopes": ["team"],
                "context": ["channelTab"],
            }
        ],
        "permissions": ["identity"],
        "validDomains": [host],
        "webApplicationInfo": {"id": client_id, "resource": id_uri},
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("cmp-teams.zip"))
    ap.add_argument("--developer", default="Clone My Professor")
    ap.add_argument("--privacy-url")
    ap.add_argument("--terms-url")
    args = ap.parse_args()
    public_url = s.public_url.rstrip("/")
    if not s.teams_app_client_id:
        raise SystemExit("set TEAMS_APP_CLIENT_ID (the Entra app's client id) in .env first")
    if not public_url.startswith("https://"):
        print(f"warning: PUBLIC_URL is {public_url}; Teams only loads https pages")
    host = urlsplit(public_url).netloc
    id_uri = s.teams_app_id_uri or f"api://{host}/{s.teams_app_client_id}"
    m = manifest(
        public_url,
        s.teams_app_client_id,
        id_uri,
        args.developer,
        args.privacy_url or public_url,
        args.terms_url or public_url,
    )
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(m, ensure_ascii=False, indent=2))
        z.writestr("color.png", _icon(192, outline=False))
        z.writestr("outline.png", _icon(32, outline=True))
    print(f"wrote {args.out} (app id {m['id']}, SSO resource {id_uri})")
    print("In .env, let Teams embed CMP:")
    print(f'  CMP_FRAME_ANCESTORS="{TEAMS_FRAME_ANCESTORS}"   (plus any LMS origins already there)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
