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
import sys
from typing import Iterable, List, Optional, Tuple

import requests

BASE_URL = "http://www.mytravelmap.xyz/api/v1/"
TEST_APP_KEY = "TESTAPPKEY000000"


def load_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Read (APP_KEY, APP_SHARED_SECRET) from an optional ``secrets.py`` file.

    The file lives next to this module and is entirely optional: if it does not
    exist (or doesn't define the names) ``None`` is returned for the missing
    values and callers fall back to the testing app key. It is loaded by path so
    it never clashes with Python's stdlib ``secrets`` module. Expected contents::

        APP_KEY = "yourAppKey"
        APP_SHARED_SECRET = "yourSharedSecret"
    """
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.py")
    if not os.path.isfile(path):
        return None, None

    spec = importlib.util.spec_from_file_location("_mtm_secrets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        getattr(module, "APP_KEY", None),
        getattr(module, "APP_SHARED_SECRET", None),
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


# Re-export the helper at module level for convenience.
geo_value = _geo_value


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
    parser.add_argument("--user-id", help="MyTravelMap user ID.")
    parser.add_argument("--user-color-born", help="Color for user's born country.")
    parser.add_argument("--user-color-lived", help="Color for user's lived countries.")
    parser.add_argument("--user-color-visited", help="Color for user's visited countries.")
    parser.add_argument("-o", "--output", default="world.png",
                        help="File to write the result to ('-' for stdout).")
    args = parser.parse_args(argv)

    # Resolve credentials: CLI args win, then secrets.py, then the testing key.
    secret_key, secret_secret = load_credentials()
    app_key = args.app_key or secret_key or TEST_APP_KEY
    app_secret = args.app_secret or secret_secret

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
            user_id=args.user_id,
            user_color_born=args.user_color_born,
            user_color_lived=args.user_color_lived,
            user_color_visited=args.user_color_visited,
        )
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.file_type == "json":
        print(response.text)
    elif args.output == "-":
        sys.stdout.buffer.write(response.content)
    else:
        with open(args.output, "wb") as fh:
            fh.write(response.content)
        print(f"Saved {len(response.content)} bytes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
