#!/usr/bin/env python3
"""
Client for the MyTravelMap "Generate world map" API.

API docs: https://www.mytravelmap.xyz/developers

The service renders a world map (PNG/BMP image, or JSON metadata) with selected
countries colored and/or pins placed on it.

Authentication:
  - Every request needs an application key (``appKey``).
  - With your own key you must also sign the request (``sig`` parameter), using
    the application shared secret (APP_SHARED_SECRET) which is *never* sent over
    the network.
  - The special key ``TESTAPPKEY000000`` needs no signature, but is globally
    shared and rate-limited (HTTP 429 when the per-minute limit is exceeded).

Signature algorithm (see docs):
  1. Build "name=value" for every request parameter except ``sig``.
  2. Sort those strings alphabetically (ascending).
  3. Concatenate them, then append APP_SHARED_SECRET.
  4. sig = sha1(thatString)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import requests

BASE_URL = "http://www.mytravelmap.xyz/api/v1/"
TEST_APP_KEY = "TESTAPPKEY000000"


def load_credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Read (APP_KEY, APP_SHARED_SECRET, USER_ID) from an optional ``secrets.py``.

    The file lives next to this module and is entirely optional: if it does not
    exist (or doesn't define the names) ``None`` is returned for the missing
    values and callers fall back to the testing app key. It is loaded by path so
    it never clashes with Python's stdlib ``secrets`` module. Expected contents::

        APP_KEY = "yourAppKey"
        APP_SHARED_SECRET = "yourSharedSecret"
        USER_ID = "your-public-user-id"  # last part of your Permalink
    """
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.py")
    if not os.path.isfile(path):
        return None, None, None

    spec = importlib.util.spec_from_file_location("_mtm_secrets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        getattr(module, "APP_KEY", None),
        getattr(module, "APP_SHARED_SECRET", None),
        getattr(module, "USER_ID", None),
    )

# A param list is a list of (name, value) pairs. A list (not a dict) is used
# because parameters such as `geo` and `pin` may legitimately appear several
# times in a single request.
ParamList = List[Tuple[str, str]]


def calculate_signature(params: ParamList, app_shared_secret: str) -> str:
    """Return the SHA1 request signature for the given parameters.

    ``params`` are (name, value) pairs using the *raw* (un-encoded) values,
    exactly as the docs specify. The ``sig`` parameter itself is ignored if
    present.
    """
    pieces = [f"{name}={value}" for name, value in params if name != "sig"]
    pieces.sort()
    to_sign = "".join(pieces) + app_shared_secret
    return hashlib.sha1(to_sign.encode("utf-8")).hexdigest()


def _geo_value(color: str, coords: Iterable[Tuple[float, float]]) -> str:
    """Build a `geo`/`pin` value: 'color$lat,lon!lat,lon!...'."""
    points = "!".join(f"{lat},{lon}" for lat, lon in coords)
    if not points:
        raise ValueError("at least one (lat, lon) coordinate is required")
    return f"{color}${points}"


class MyTravelMapClient:
    """Thin client for the MyTravelMap world-map generation API."""

    def __init__(
        self,
        app_key: str = TEST_APP_KEY,
        app_shared_secret: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: int = 60,
    ):
        """
        :param app_key: your application key, or ``TESTAPPKEY000000`` for testing.
        :param app_shared_secret: required to sign requests when using your own
            key. Leave ``None`` when using the testing key.
        :param base_url: API base URL (override only if instructed to).
        :param timeout: per-request timeout in seconds.
        """
        self.app_key = app_key
        self.app_shared_secret = app_shared_secret
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

        if app_key != TEST_APP_KEY and not app_shared_secret:
            raise ValueError(
                "app_shared_secret is required to sign requests when not using "
                "the testing app key (TESTAPPKEY000000)."
            )

    def _build_params(self, extra: ParamList) -> ParamList:
        """Prepend appKey and append the signature where required."""
        params: ParamList = [("appKey", self.app_key)]
        params.extend((k, str(v)) for k, v in extra if v is not None)

        if self.app_key != TEST_APP_KEY:
            sig = calculate_signature(params, self.app_shared_secret or "")
            params.append(("sig", sig))
        return params

    def generate_world_map(
        self,
        width: int,
        height: int,
        file_type: str = "png",
        *,
        bg_color: Optional[str] = None,
        fg_color: Optional[str] = None,
        dither: Optional[str] = None,
        geo: Optional[Iterable[str]] = None,
        pin: Optional[Iterable[str]] = None,
        pin_scale: Optional[float] = None,
        pin_label_color: Optional[str] = None,
        pin_label_font: Optional[str] = None,
        pin_label_locale: Optional[str] = None,
        pin_label_size: Optional[int] = None,
        pin_label_weight: Optional[str] = None,
        user_id: Optional[str] = None,
        user_color_born: Optional[str] = None,
        user_color_lived: Optional[str] = None,
        user_color_visited: Optional[str] = None,
    ) -> requests.Response:
        """Call ``GET world.{file_type}`` and return the raw response.

        ``file_type`` is one of ``json``, ``png`` or ``bmp``.
        ``geo`` / ``pin`` are iterables of values formatted as
        ``'color$lat,lon!lat,lon!...'`` (use :func:`geo_value` to build them).
        Colors are 6 hex digits (RGB) or 8 hex digits (RGBA).
        """
        if file_type not in ("json", "png", "bmp"):
            raise ValueError("file_type must be one of: json, png, bmp")
        if not (1 <= width <= 2050) or not (1 <= height <= 2050):
            raise ValueError("width and height must be between 1 and 2050 px")

        extra: ParamList = [("width", str(width)), ("height", str(height))]

        optional = {
            "bgColor": bg_color,
            "fgColor": fg_color,
            "dither": dither,
            "pinScale": pin_scale,
            "pinLabelColor": pin_label_color,
            "pinLabelFont": pin_label_font,
            "pinLabelLocale": pin_label_locale,
            "pinLabelSize": pin_label_size,
            "pinLabelWeight": pin_label_weight,
            "userId": user_id,
            "userColorBorn": user_color_born,
            "userColorLived": user_color_lived,
            "userColorVisited": user_color_visited,
        }
        extra.extend((k, v) for k, v in optional.items() if v is not None)

        for value in geo or []:
            extra.append(("geo", value))
        for value in pin or []:
            extra.append(("pin", value))

        params = self._build_params(extra)
        url = f"{self.base_url}world.{file_type}"

        response = requests.get(url, params=params, timeout=self.timeout)
        if response.status_code == 429:
            raise RuntimeError(
                "429 Too Many Requests - the testing app key rate limit was "
                "exceeded. Try again shortly or use your own app key."
            )
        response.raise_for_status()
        return response

    def get_user_countries(
        self, user_id: str, locale: Optional[str] = None
    ) -> dict:
        """Call ``GET countries.json`` and return the parsed JSON payload.

        Returns the user's countries grouped by status (BORN, LIVED, VISITED,
        WANT_TO_VISIT); each country has an ``isoCode`` and localized ``name``.
        ``locale`` selects the language of the names (default ``en``).
        """
        extra: ParamList = [("userId", user_id)]
        if locale:
            extra.append(("locale", locale))

        params = self._build_params(extra)
        url = f"{self.base_url}countries.json"

        response = requests.get(url, params=params, timeout=self.timeout)
        if response.status_code == 429:
            raise RuntimeError(
                "429 Too Many Requests - the testing app key rate limit was "
                "exceeded. Try again shortly or use your own app key."
            )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "OK":
            errors = "; ".join(data.get("errors", [])) or "unknown error"
            raise RuntimeError(f"countries.json returned an error: {errors}")
        return data


# Re-export the helper at module level for convenience.
geo_value = _geo_value

# Status groups returned by countries.json, in display order.
STATUS_ORDER = ("BORN", "LIVED", "VISITED", "WANT_TO_VISIT")


def _base_iso(iso_code: str) -> str:
    """Country code for an entry, dropping any subdivision suffix.

    ``AU_NS`` / ``AU_VI`` -> ``AU``; ``IT`` -> ``IT``. Used to tell apart
    "states" (every entry) from "countries" (distinct base ISO codes), the same
    distinction scrape_countries.py makes.
    """
    return iso_code.split("_", 1)[0].upper() if iso_code else ""


def add_totals(data: dict) -> dict:
    """Return the countries payload augmented with a ``totals`` section.

    ``states`` counts list entries (subdivisions counted separately);
    ``countries`` counts distinct base ISO codes. Totals are reported per status
    group and overall, mirroring what scrape_countries.py prints per continent.
    """
    groups = data.get("countries", {}) or {}
    by_status: Dict[str, Dict[str, int]] = {}
    total_states = 0
    all_isos = set()
    for status in list(STATUS_ORDER) + [s for s in groups if s not in STATUS_ORDER]:
        items = groups.get(status, [])
        isos = {_base_iso(c.get("isoCode", "")) for c in items if c.get("isoCode")}
        by_status[status] = {"states": len(items), "countries": len(isos)}
        total_states += len(items)
        all_isos |= isos

    result = dict(data)
    result["totals"] = {
        "byStatus": by_status,
        "states": total_states,
        "countries": len(all_isos),
    }
    return result


def format_countries_summary(data: dict) -> str:
    """One-line-per-group human summary of the totals in ``data``."""
    totals = data.get("totals", {})
    lines = []
    for status, counts in totals.get("byStatus", {}).items():
        lines.append(
            f"  {status}: {counts['states']} states, {counts['countries']} countries"
        )
    lines.append(
        f"  TOTAL: {totals.get('states', 0)} states, "
        f"{totals.get('countries', 0)} countries"
    )
    return "\n".join(lines)


def format_countries_text(data: dict) -> str:
    """Human-readable listing of every country, grouped by status.

    Mirrors scrape_countries.py: each group header shows its state/country
    counts, and overall totals are printed at the end.
    """
    data = data if "totals" in data else add_totals(data)
    groups = data.get("countries", {}) or {}
    totals = data["totals"]
    lines: List[str] = []
    for status, items in groups.items():
        counts = totals["byStatus"].get(status, {"states": 0, "countries": 0})
        header = (
            f"{status} ({counts['states']} states, {counts['countries']} countries)"
        )
        lines.append(f"\n{header}")
        lines.append("-" * len(header))
        for c in items:
            iso = c.get("isoCode", "")
            prefix = f"[{iso}] " if iso else ""
            lines.append(f"  {prefix}{c.get('name', '')}")
    lines.append(f"\nTotal states: {totals.get('states', 0)}")
    lines.append(f"Total countries: {totals.get('countries', 0)}")
    return "\n".join(lines).lstrip("\n")


def add_date_suffix(path: str) -> str:
    """Insert today's date before the extension: world.png -> world-2026-06-16.png."""
    import datetime
    import os

    stem, ext = os.path.splitext(path)
    today = datetime.date.today().isoformat()  # YYYY-MM-DD
    return f"{stem}-{today}{ext}"


def _parse_geo_arg(raw: str) -> str:
    """Validate a CLI --geo/--pin value of the form 'color$lat,lon!lat,lon'."""
    if "$" not in raw:
        raise argparse.ArgumentTypeError(
            f"invalid value {raw!r}: expected 'color$lat,lon!lat,lon!...'"
        )
    return raw


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a world map via the MyTravelMap API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--app-key", default=None,
                        help="Application key. Falls back to secrets.py, "
                             "then to the testing key.")
    parser.add_argument("--app-secret", default=None,
                        help="APP_SHARED_SECRET (required with your own app key). "
                             "Falls back to secrets.py.")
    parser.add_argument("--width", type=int, default=2048, help="Image width px (max 2050).")
    parser.add_argument("--height", type=int, default=1536, help="Image height px (max 2050).")
    parser.add_argument("--type", dest="file_type", default="png",
                        choices=["json", "png", "bmp"], help="Output format.")
    parser.add_argument("--bg-color", help="Sea/background color (RGB or RGBA hex).")
    parser.add_argument("--fg-color", help="Land/foreground color (RGB or RGBA hex).")
    parser.add_argument("--dither", choices=["yes", "no"], help="Apply dithering.")
    parser.add_argument("--geo", action="append", type=_parse_geo_arg, default=[],
                        help="Country fill: 'color$lat,lon!...' (repeatable).")
    parser.add_argument("--pin", action="append", type=_parse_geo_arg, default=[],
                        help="Pin: 'color$lat,lon!...' (repeatable).")
    parser.add_argument("--pin-scale", type=float, help="Pin size (default 0.3).")
    parser.add_argument("--pin-label-size", type=int,
                        help="Country-name label size (pt). Must be >0 to show labels.")
    parser.add_argument("--pin-label-color", help="Label color (RGB/RGBA hex).")
    parser.add_argument("--pin-label-font", help="Label font family.")
    parser.add_argument("--pin-label-locale", help="Label locale, e.g. en, it, ru.")
    parser.add_argument("--pin-label-weight", choices=["normal", "bold"],
                        help="Label font weight.")
    parser.add_argument("--user-id", help="MyTravelMap public user ID "
                        "(last part of your Permalink). Falls back to secrets.py.")
    parser.add_argument("--user-color-born", help="Color for user's born country.")
    parser.add_argument("--user-color-lived", help="Color for user's lived countries.")
    parser.add_argument("--user-color-visited", help="Color for user's visited countries.")
    parser.add_argument("--countries", action="store_true",
                        help="Fetch your countries list (countries.json) with totals "
                             "instead of generating a map image.")
    parser.add_argument("--locale", help="Language for country names in --countries "
                        "mode (default en).")
    parser.add_argument("-o", "--output", default=None,
                        help="File to write the result to ('-' for stdout). "
                             "Defaults to world.png, or countries.json with --countries.")
    parser.add_argument("--date-suffix", action="store_true",
                        help="Append today's date to the filename, "
                             "e.g. world-2026-06-16.png.")
    parser.add_argument("-p", "--print", action="store_true", dest="also_print",
                        help="Also print your countries list to the screen.")
    args = parser.parse_args(argv)

    # Resolve credentials: CLI args win, then secrets.py, then the testing key.
    secret_key, secret_secret, secret_user_id = load_credentials()
    app_key = args.app_key or secret_key or TEST_APP_KEY
    app_secret = args.app_secret or secret_secret
    user_id = args.user_id or secret_user_id

    if args.countries:
        return _run_countries(app_key, app_secret, user_id, args)

    try:
        client = MyTravelMapClient(app_key=app_key, app_shared_secret=app_secret)
        response = client.generate_world_map(
            width=args.width,
            height=args.height,
            file_type=args.file_type,
            bg_color=args.bg_color,
            fg_color=args.fg_color,
            dither=args.dither,
            geo=args.geo,
            pin=args.pin,
            pin_scale=args.pin_scale,
            pin_label_color=args.pin_label_color,
            pin_label_font=args.pin_label_font,
            pin_label_locale=args.pin_label_locale,
            pin_label_size=args.pin_label_size,
            pin_label_weight=args.pin_label_weight,
            user_id=user_id,
            user_color_born=args.user_color_born,
            user_color_lived=args.user_color_lived,
            user_color_visited=args.user_color_visited,
        )
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = args.output or "world.png"
    if args.file_type == "json":
        print(response.text)
    elif output == "-":
        sys.stdout.buffer.write(response.content)
    else:
        if args.date_suffix:
            output = add_date_suffix(output)
        with open(output, "wb") as fh:
            fh.write(response.content)
        print(f"Saved {len(response.content)} bytes to {output}")

    if args.also_print:
        _print_countries(app_key, app_secret, user_id, args.locale)
    return 0


def _print_countries(
    app_key: str,
    app_secret: Optional[str],
    user_id: Optional[str],
    locale: Optional[str],
) -> None:
    """Fetch and print the user's countries listing to stdout (best effort)."""
    if not user_id:
        print(
            "Note: -p needs a user ID to list your countries. Pass --user-id or "
            "set USER_ID in secrets.py.",
            file=sys.stderr,
        )
        return
    try:
        client = MyTravelMapClient(app_key=app_key, app_shared_secret=app_secret)
        data = add_totals(client.get_user_countries(user_id, locale=locale))
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Note: could not list countries: {exc}", file=sys.stderr)
        return
    print(format_countries_text(data))


def _run_countries(
    app_key: str,
    app_secret: Optional[str],
    user_id: Optional[str],
    args: argparse.Namespace,
) -> int:
    """Fetch countries.json, add totals, and save/print it."""
    if not user_id:
        print(
            "Error: --countries needs a user ID. Pass --user-id or set USER_ID "
            "in secrets.py (the last part of your Permalink).",
            file=sys.stderr,
        )
        return 2

    try:
        client = MyTravelMapClient(app_key=app_key, app_shared_secret=app_secret)
        data = client.get_user_countries(user_id, locale=args.locale)
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    data = add_totals(data)
    text = json.dumps(data, ensure_ascii=False, indent=2)

    output = args.output or "countries.json"
    if output == "-":
        print(text)
    else:
        if args.date_suffix:
            output = add_date_suffix(output)
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"Saved countries to {output}", file=sys.stderr)
        if args.also_print:
            print(format_countries_text(data))
    print(format_countries_summary(data), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
