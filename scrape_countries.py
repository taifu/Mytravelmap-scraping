#!/usr/bin/env python3
"""
Log in to mytravelmap.xyz and scrape the list of countries on your travel map.

Credentials are read from CLI flags, else from ``secrets.py`` (next to this
script)::

    EMAIL = "you@example.com"
    PASSWORD = "your-password"

The site is an Apache Wicket application: the home page (``/?0``) is stateful and
its login form posts to a session/version-specific URL. The flow is therefore:

    1. Open a session and GET the home page (sets the JSESSIONID cookie and shows
       the login form when logged out).
    2. Read the email-login form's live action URL and any hidden fields, then
       POST the credentials to it.
    3. GET the home page again (now authenticated) and parse the countries table.

The logged-in home page lists every country on the map inside
``table#continents``; each ``li.country`` holds the localized country name and a
flag whose filename is the ISO code. Categories (visited / lived / born) are not
distinguished in that list, so all marked countries are returned.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.mytravelmap.xyz/"
HOME_URL = BASE_URL + "?0"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_FLAG_ISO_RE = re.compile(r"/flags_iso/\d+/([a-z0-9]+)\.png", re.IGNORECASE)


def load_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Read (EMAIL, PASSWORD) from the optional ``secrets.py`` file by path.

    Accepts ``EMAIL`` or ``LOGIN`` for the e-mail. Returns ``None`` for whatever
    is missing. Loaded by path so it never clashes with the stdlib ``secrets``.
    """
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.py")
    if not os.path.isfile(path):
        return None, None

    spec = importlib.util.spec_from_file_location("_mtm_secrets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    email = getattr(module, "EMAIL", None) or getattr(module, "LOGIN", None)
    password = getattr(module, "PASSWORD", None)
    return email, password


class MyTravelMapSession:
    """Authenticated session for scraping a mytravelmap.xyz account."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _get_soup(self, url: str) -> Tuple[BeautifulSoup, requests.Response]:
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser"), resp

    @staticmethod
    def _is_logged_in(soup: BeautifulSoup) -> bool:
        # The countries table exists on both the logged-out and logged-in pages,
        # so it is NOT a reliable marker. Logged in <=> the e-mail login form is
        # gone (equivalently, the logout link is present).
        has_login_form = soup.find("form", action=re.compile(r"emailLoginPanel-form")) is not None
        has_logout = soup.find("a", class_="account-menu-logout") is not None
        return has_logout or not has_login_form

    def login(self, email: str, password: str) -> None:
        """Authenticate. Raises RuntimeError on failure."""
        soup, resp = self._get_soup(HOME_URL)
        if self._is_logged_in(soup):
            return  # already authenticated (e.g. reused session)

        form = soup.find("form", action=re.compile(r"emailLoginPanel-form"))
        if form is None:
            raise RuntimeError(
                "Could not find the e-mail login form - the site layout may have "
                "changed."
            )

        # Collect all form inputs (incl. Wicket hidden fields) then set creds.
        data: Dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                data[name] = inp.get("value", "") or ""
        data["email"] = email
        data["password"] = password
        data.setdefault("submitBtn", "Log in")

        post_url = urljoin(resp.url, form["action"])
        post_resp = self.session.post(post_url, data=data, timeout=self.timeout)
        post_resp.raise_for_status()

        # Re-fetch the home page and confirm we are authenticated.
        soup, _ = self._get_soup(HOME_URL)
        if not self._is_logged_in(soup):
            raise RuntimeError(
                "Login failed - check the EMAIL/PASSWORD credentials."
            )

    def get_countries(self) -> List[Dict[str, str]]:
        """Return the marked countries as a list of dicts.

        Each item: ``{"continent": ..., "name": ..., "iso": ...}``.
        """
        soup, _ = self._get_soup(HOME_URL)
        if not self._is_logged_in(soup):
            raise RuntimeError("Not logged in - call login() first.")

        table = soup.find("table", id="continents")
        if table is None:
            raise RuntimeError("Countries table not found on the page.")

        countries: List[Dict[str, str]] = []
        body = table.find("tbody") or table
        for cell in body.find_all("td"):
            heading = cell.find("h2", class_="continent")
            continent = heading.get_text(strip=True) if heading else ""
            for item in cell.select("ul.countries li.country"):
                name_span = item.find("span")
                name = name_span.get_text(strip=True) if name_span else ""
                if not name:
                    continue
                flag = item.find("img", class_="flag")
                iso = ""
                if flag and flag.get("src"):
                    m = _FLAG_ISO_RE.search(flag["src"])
                    if m:
                        iso = m.group(1).lower()
                countries.append(
                    {"continent": continent, "name": name, "iso": iso}
                )
        return countries


def build_output_path(output: Optional[str], as_json: bool, date_suffix: bool) -> Optional[str]:
    """Resolve the output filename.

    ``output`` is the value of --output: ``None`` -> stdout, ``""`` -> default
    name, otherwise the given path. When ``date_suffix`` is set, today's date is
    inserted before the extension, e.g. ``countries.txt`` -> ``countries-2026-06-12.txt``.
    """
    import datetime
    import os

    if output is None:
        return None  # stdout

    path = output or ("countries.json" if as_json else "countries.txt")
    if date_suffix:
        stem, ext = os.path.splitext(path)
        if not ext:  # no extension -> pick one based on format
            ext = ".json" if as_json else ".txt"
        today = datetime.date.today().isoformat()  # YYYY-MM-DD
        path = f"{stem}-{today}{ext}"
    return path


def format_text(countries: List[Dict[str, str]]) -> str:
    """Human-readable listing grouped by continent."""
    lines: List[str] = []
    by_continent: Dict[str, List[Dict[str, str]]] = {}
    for c in countries:
        by_continent.setdefault(c["continent"], []).append(c)

    for continent, items in by_continent.items():
        n_countries = len({c["iso"] for c in items if c["iso"]})
        header = f"{continent} ({len(items)} states, {n_countries} countries)"
        lines.append(f"\n{header}")
        lines.append("-" * len(header))
        for c in items:
            iso = f"[{c['iso']}] " if c["iso"] else ""
            lines.append(f"  {iso}{c['name']}")
    distinct_countries = len({c["iso"] for c in countries if c["iso"]})
    lines.append(f"\nTotal states: {len(countries)}")
    lines.append(f"Total countries: {distinct_countries}")
    return "\n".join(lines).lstrip("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Log in to mytravelmap.xyz and list your countries.",
    )
    parser.add_argument("--email", help="Login e-mail (overrides secrets.py).")
    parser.add_argument("--password", help="Login password (overrides secrets.py).")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of a text listing.")
    parser.add_argument("-o", "--output", nargs="?", const="",
                        help="Write output to a file instead of stdout. With no "
                             "value, defaults to countries.txt (or .json).")
    parser.add_argument("--date-suffix", action="store_true",
                        help="Append today's date to the filename, "
                             "e.g. countries-2026-06-12.txt.")
    parser.add_argument("-p", "--print", action="store_true", dest="also_print",
                        help="Also print to screen when saving to a file with -o.")
    args = parser.parse_args(argv)

    if args.date_suffix and args.output is None:
        print(
            "Error: --date-suffix requires saving to a file; pass -o/--output.",
            file=sys.stderr,
        )
        return 2

    sec_email, sec_password = load_credentials()
    email = args.email or sec_email
    password = args.password or sec_password
    if not email or not password:
        print(
            "Error: missing credentials. Provide --email/--password or set "
            "EMAIL and PASSWORD in secrets.py.",
            file=sys.stderr,
        )
        return 2

    try:
        client = MyTravelMapSession()
        client.login(email, password)
        countries = client.get_countries()
    except (RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = (
        json.dumps(countries, ensure_ascii=False, indent=2)
        if args.json
        else format_text(countries)
    )
    out_path = build_output_path(args.output, args.json, args.date_suffix)
    if out_path is not None:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(output + "\n")
        print(f"Wrote {len(countries)} countries to {out_path}", file=sys.stderr)
        if args.also_print:
            print(output)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
