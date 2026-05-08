#!/usr/bin/env python3
"""papis - local mirror & invocation toolkit for public-apis.

Subcommands:
  build              Fetch upstream README, parse, write SQLite + JSON dataset.
  search [QUERY]     Search APIs by name/description/category.
  show NAME          Show full record for an API.
  cats               List categories with counts.
  vault set NAME     Store an API key in macOS Keychain.
  vault get NAME     Print stored key (use sparingly).
  vault list         List names of APIs with stored keys.
  vault rm NAME      Remove stored key.
  call URL           Call an API URL (with optional vault-attached auth).

All subcommands except `build` and `call` work fully offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "apis.db"
JSON_PATH = DATA_DIR / "apis.json"
INDEX_PATH = Path.home() / ".papis" / "index.json"
KEYCHAIN_SERVICE = "papis"

README_URL = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
USER_AGENT = "papis-local-mirror/0.1"


def fetch_readme(url=README_URL, timeout=30.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


ROW = re.compile(
    r"^\|\s*\[(?P<name>[^\]]+)\]\((?P<url>[^)]+)\)\s*"
    r"\|(?P<desc>[^|]*)"
    r"\|(?P<auth>[^|]*)"
    r"\|(?P<https>[^|]*)"
    r"\|(?P<cors>[^|\n]*?)\s*(?:\|.*)?$"
)
HEADING = re.compile(r"^###\s+(?P<title>.+?)\s*$")
SKIP_SECTIONS = {"APILayer APIs"}


def parse_readme(md):
    rows = []
    current_cat = None
    in_body = False
    for line in md.splitlines():
        if not in_body:
            if line.startswith("### Animals"):
                in_body = True
                current_cat = "Animals"
                continue
            else:
                continue
        m = HEADING.match(line)
        if m:
            current_cat = m.group("title").strip()
            continue
        if current_cat is None or current_cat in SKIP_SECTIONS:
            continue
        m = ROW.match(line)
        if not m:
            continue
        def clean(s):
            return s.strip().strip("`").strip()
        rows.append({
            "name": clean(m.group("name")),
            "url": m.group("url").strip(),
            "description": clean(m.group("desc")),
            "category": current_cat,
            "auth": clean(m.group("auth")),
            "https": clean(m.group("https")),
            "cors": clean(m.group("cors")),
        })
    return rows


DDL = """
CREATE TABLE IF NOT EXISTS apis (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL,
  auth TEXT NOT NULL,
  https TEXT NOT NULL,
  cors TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apis_category ON apis(category);
CREATE INDEX IF NOT EXISTS idx_apis_name ON apis(name);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def write_dataset(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO apis(name,url,description,category,auth,https,cors) "
            "VALUES(:name,:url,:description,:category,:auth,:https,:cors)",
            records,
        )
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            [("updated_at", now), ("count", str(len(records)))],
        )
        conn.commit()
    finally:
        conn.close()
    JSON_PATH.write_text(
        json.dumps(
            {"updated_at": now, "count": len(records), "apis": records},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def load_db():
    if not DB_PATH.exists():
        die("dataset missing - run: python3 papis.py build")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def keychain_set(name, secret):
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name],
        capture_output=True,
    )
    subprocess.run(
        ["security", "add-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", name, "-w", secret, "-U"],
        check=True, capture_output=True,
    )


def keychain_get(name):
    r = subprocess.run(
        ["security", "find-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def keychain_rm(name):
    r = subprocess.run(
        ["security", "delete-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", name],
        capture_output=True,
    )
    return r.returncode == 0


def index_load():
    if not INDEX_PATH.exists():
        return {}
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def index_save(idx):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    INDEX_PATH.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(INDEX_PATH, 0o600)


def attach_auth(url, headers, key, style, param):
    if style == "header":
        headers[param] = key
    elif style == "bearer":
        headers["Authorization"] = "Bearer " + key
    elif style == "query":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{param}={urllib.parse.quote(key)}"
    elif style == "none":
        pass
    else:
        die(f"unknown auth style: {style}")
    return url, headers


def http_call(url, method, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.getheaders()), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read() or b"")


def die(msg, code=1):
    print(f"papis: {msg}", file=sys.stderr)
    sys.exit(code)


def cmd_build(args):
    print("fetching upstream README ...", file=sys.stderr)
    md = fetch_readme()
    print("parsing ...", file=sys.stderr)
    records = parse_readme(md)
    if not records:
        die("parse produced 0 records - upstream layout may have changed")
    print(f"writing {len(records)} records to data/", file=sys.stderr)
    write_dataset(records)
    print(f"done. {len(records)} APIs.", file=sys.stderr)


def cmd_search(args):
    conn = load_db()
    sql = "SELECT name,category,auth,https,cors,description,url FROM apis WHERE 1=1"
    params = []
    if args.query:
        like = f"%{args.query}%"
        sql += " AND (name LIKE ? OR description LIKE ? OR category LIKE ?)"
        params.extend([like, like, like])
    if args.category:
        sql += " AND category = ?"
        params.append(args.category)
    if args.no_auth:
        sql += " AND auth = 'No'"
    if args.https:
        sql += " AND https = 'Yes'"
    if args.cors:
        sql += " AND cors = 'Yes'"
    sql += " ORDER BY category, name LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("(no results)", file=sys.stderr)
        return
    for r in rows:
        flags = ["no-auth" if r["auth"] == "No" else f"auth={r['auth']}"]
        if r["https"] == "Yes":
            flags.append("https")
        if r["cors"] == "Yes":
            flags.append("cors")
        print(f"[{r['category']}] {r['name']} ({', '.join(flags)})")
        print(f"  {r['description']}")
        print(f"  {r['url']}")
        print()
    print(f"-- {len(rows)} result(s)", file=sys.stderr)


def cmd_show(args):
    conn = load_db()
    row = conn.execute(
        "SELECT * FROM apis WHERE name = ? COLLATE NOCASE LIMIT 1", (args.name,)
    ).fetchone()
    if not row:
        die(f"no API named: {args.name}")
    for k in ("name", "category", "auth", "https", "cors", "url", "description"):
        print(f"{k:>12}: {row[k]}")


def cmd_cats(args):
    conn = load_db()
    rows = conn.execute(
        "SELECT category, COUNT(*) AS n FROM apis GROUP BY category ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print(f"{r['n']:4}  {r['category']}")


def cmd_vault_set(args):
    secret = args.key or getpass.getpass(f"API key for {args.name}: ")
    if not secret:
        die("empty key")
    keychain_set(args.name, secret)
    idx = index_load()
    idx[args.name] = {
        "auth_style": args.auth_style,
        "auth_param": args.auth_param,
        "set_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    index_save(idx)
    print(f"stored. {args.name} -> keychain (style={args.auth_style}, param={args.auth_param})")


def cmd_vault_get(args):
    val = keychain_get(args.name)
    if val is None:
        die(f"no key stored for: {args.name}")
    print(val)


def cmd_vault_list(args):
    idx = index_load()
    if not idx:
        print("(none)", file=sys.stderr)
        return
    for name, meta in sorted(idx.items()):
        print(f"{name}\tstyle={meta.get('auth_style')}\tparam={meta.get('auth_param')}\tset_at={meta.get('set_at')}")


def cmd_vault_rm(args):
    ok = keychain_rm(args.name)
    idx = index_load()
    idx.pop(args.name, None)
    index_save(idx)
    print("removed." if ok else "(not in keychain; index cleared)")


def cmd_call(args):
    url = args.url
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for h in args.header or []:
        if ":" not in h:
            die(f"bad --header {h!r} (use 'Name: value')")
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()

    if args.name:
        meta = index_load().get(args.name)
        if not meta:
            die(f"vault has no entry for {args.name}; run: papis vault set {args.name}")
        key = keychain_get(args.name)
        if key is None:
            die(f"keychain entry missing for {args.name}; re-run vault set")
        url, headers = attach_auth(
            url, headers, key,
            args.auth_style or meta["auth_style"],
            args.auth_param or meta["auth_param"],
        )

    body = args.data.encode("utf-8") if args.data else None
    if body and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    status, resp_headers, payload = http_call(
        url, args.method.upper(), headers, body, args.timeout,
    )
    print(f"{args.method.upper()} {url} -> {status}", file=sys.stderr)
    if args.show_headers:
        for k, v in resp_headers.items():
            print(f"{k}: {v}", file=sys.stderr)
        print("", file=sys.stderr)
    sys.stdout.buffer.write(payload)
    if not payload.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    if status >= 400:
        sys.exit(2)


def build_parser():
    p = argparse.ArgumentParser(
        prog="papis", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("build", help="refresh local dataset from upstream README")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("search", help="search APIs")
    sp.add_argument("query", nargs="?", help="substring across name/description/category")
    sp.add_argument("--category")
    sp.add_argument("--no-auth", action="store_true")
    sp.add_argument("--https", action="store_true")
    sp.add_argument("--cors", action="store_true")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", help="show one API record")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("cats", help="list categories with counts")
    sp.set_defaults(func=cmd_cats)

    vp = sub.add_parser("vault", help="manage stored API keys (macOS Keychain)")
    vsub = vp.add_subparsers(dest="vault_cmd", required=True)

    vs = vsub.add_parser("set")
    vs.add_argument("name")
    vs.add_argument("--key", help="API key (omit for interactive prompt)")
    vs.add_argument("--auth-style", choices=["header", "query", "bearer", "none"], default="header")
    vs.add_argument("--auth-param", default="X-API-Key")
    vs.set_defaults(func=cmd_vault_set)

    vg = vsub.add_parser("get")
    vg.add_argument("name")
    vg.set_defaults(func=cmd_vault_get)

    vl = vsub.add_parser("list")
    vl.set_defaults(func=cmd_vault_list)

    vr = vsub.add_parser("rm")
    vr.add_argument("name")
    vr.set_defaults(func=cmd_vault_rm)

    sp = sub.add_parser("call", help="HTTP call with optional vault-attached auth")
    sp.add_argument("url")
    sp.add_argument("--name", help="vault entry to attach auth from")
    sp.add_argument("--method", default="GET")
    sp.add_argument("--data", help="request body string")
    sp.add_argument("--header", action="append", help="extra header 'Name: value' (repeatable)")
    sp.add_argument("--auth-style", choices=["header", "query", "bearer", "none"])
    sp.add_argument("--auth-param")
    sp.add_argument("--show-headers", action="store_true")
    sp.add_argument("--timeout", type=float, default=30.0)
    sp.set_defaults(func=cmd_call)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
