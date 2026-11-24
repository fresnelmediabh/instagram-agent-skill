#!/usr/bin/env python3
"""ig - a command line for your own Instagram account, on the official Graph API.

Stdlib only. No scraping, no private endpoints, no password. You need an
Instagram professional (Business or Creator) account and an access token from
the Instagram API with Instagram Login (Meta developer dashboard).

Anything that posts, sends or deletes is a DRY RUN until you add --yes.
Output is JSON on stdout (pipe to jq) unless --pretty is given.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_VERSION = os.environ.get("IG_API_VERSION", "v23.0")
BASE = os.environ.get("IG_API_BASE", "https://graph.instagram.com")
CONFIG_PATH = Path(os.environ.get("IG_CONFIG", Path.home() / ".config" / "ig-cli" / "config.json"))

MEDIA_FIELDS = "id,media_type,media_product_type,caption,permalink,timestamp,like_count,comments_count"
PROFILE_FIELDS = ("user_id,username,name,account_type,biography,website,"
                  "followers_count,follows_count,media_count,profile_picture_url")


class IGError(Exception):
    pass


# ---------------------------------------------------------------- config / auth

def load_config() -> dict:
    cfg = {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        pass
    if os.environ.get("IG_ACCESS_TOKEN"):
        cfg["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    if os.environ.get("IG_USER_ID"):
        cfg["user_id"] = os.environ["IG_USER_ID"]
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # token inside: owner only


# ---------------------------------------------------------------- http

class Client:
    def __init__(self, token: str, user_id: str | None = None):
        if not token:
            raise IGError("No access token. Run `ig login --token ...` or set IG_ACCESS_TOKEN.")
        self.token = token
        self.user_id = user_id or "me"

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None, retries: int = 2) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{BASE}/{API_VERSION}/{path.lstrip('/')}" if not path.startswith("http") else path
        data = None
        headers = {"Authorization": f"Bearer {self.token}", "User-Agent": "ig-cli/1.0"}
        if method == "GET" or method == "DELETE":
            if params:
                url += "?" + urllib.parse.urlencode(params)
        else:
            data = urllib.parse.urlencode({**params, **(body or {})}).encode()
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    err = json.loads(raw).get("error", {})
                except ValueError:
                    err = {}
                if e.code in (429, 500, 502, 503) and attempt < retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise IGError(explain(e.code, err, raw)) from None
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise IGError(f"Network error: {e.reason}") from None
        raise IGError("unreachable")

    def get(self, path, **params):
        return self.request("GET", path, params)

    def post(self, path, **body):
        return self.request("POST", path, None, body)

    def delete(self, path):
        return self.request("DELETE", path)

    def paged(self, path, limit: int, **params) -> list:
        """Follow paging.next until `limit` items are collected."""
        out, page = [], self.get(path, limit=min(limit, 50), **params)
        while True:
            out += page.get("data", [])
            nxt = page.get("paging", {}).get("next")
            if len(out) >= limit or not nxt:
                return out[:limit]
            page = self.request("GET", nxt)


def explain(status: int, err: dict, raw: str) -> str:
    code, msg = err.get("code"), err.get("message") or raw[:300]
    hint = ""
    if code == 190 or status == 401:
        hint = " -> token invalid or expired. Get a new one, or run `ig refresh`."
    elif code in (4, 17, 32, 613) or status == 429:
        hint = " -> rate limited. Wait and retry."
    elif code == 10 or code == 200:
        hint = " -> missing permission. Check the scopes on your token."
    elif code == 9007:
        hint = " -> media still processing. Retry `ig status` in a minute."
    return f"HTTP {status}, code {code}: {msg}{hint}"


# ---------------------------------------------------------------- helpers

def out(args, obj) -> None:
    print(json.dumps(obj, indent=2 if args.pretty or sys.stdout.isatty() else None, ensure_ascii=False))


def gate(args, action: str, payload: dict) -> bool:
    """Dry-run unless --yes. Returns True when the caller should really do it."""
    if args.yes:
        return True
    out(args, {"dry_run": True, "would": action, "payload": payload,
               "note": "Nothing was sent. Re-run with --yes to do it."})
    return False


def read_text(value: str | None, file: str | None) -> str | None:
    if file:
        return Path(file).read_text().strip()
    if value == "-":
        return sys.stdin.read().strip()
    return value


def need_url(u: str) -> str:
    if not u.startswith(("http://", "https://")):
        raise IGError(f"{u!r} is not a public URL. The API fetches media from a URL "
                      "you host (S3, R2, any HTTPS link); local files cannot be uploaded directly.")
    return u


def wait_container(c: Client, cid: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = c.get(cid, fields="status_code,status")
        code = s.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise IGError(f"Container {cid} {code}: {s.get('status')}")
        time.sleep(5)
    raise IGError(f"Container {cid} not ready after {timeout}s")


# ---------------------------------------------------------------- commands

def cmd_login(args, _):
    cfg = load_config()
    cfg["access_token"] = args.token
    c = Client(args.token)
    me = c.get("me", fields="user_id,username")
    cfg["user_id"] = str(me.get("user_id") or me.get("id"))
    cfg["username"] = me.get("username")
    save_config(cfg)
    out(args, {"saved": str(CONFIG_PATH), "username": cfg["username"], "user_id": cfg["user_id"]})


def cmd_refresh(args, c):
    r = c.request("GET", "https://graph.instagram.com/refresh_access_token",
                  {"grant_type": "ig_refresh_token", "access_token": c.token})
    cfg = load_config()
    cfg["access_token"] = r["access_token"]
    if not os.environ.get("IG_ACCESS_TOKEN"):
        save_config(cfg)
    out(args, {"refreshed": True, "expires_in_days": round(r.get("expires_in", 0) / 86400, 1)})


def cmd_me(args, c):
    out(args, c.get("me", fields=PROFILE_FIELDS))


def cmd_media_list(args, c):
    items = c.paged(f"{c.user_id}/media", args.limit, fields=MEDIA_FIELDS)
    if args.type:
        items = [i for i in items if i.get("media_product_type", "").upper() == args.type.upper()
                 or i.get("media_type", "").upper() == args.type.upper()]
    out(args, items)


def cmd_media_get(args, c):
    out(args, c.get(args.id, fields=MEDIA_FIELDS + ",media_url,thumbnail_url,children{id,media_type,media_url}"))


def cmd_insights(args, c):
    if args.id:
        out(args, c.get(f"{args.id}/insights", metric=args.metric or "reach,likes,comments,shares,saved,views,total_interactions"))
    else:
        out(args, c.get(f"{c.user_id}/insights",
                        metric=args.metric or "reach,views,accounts_engaged,total_interactions",
                        period="day", metric_type="total_value",
                        since=args.since, until=args.until))


def cmd_comments(args, c):
    out(args, c.paged(f"{args.media_id}/comments", args.limit,
                      fields="id,text,username,timestamp,like_count,replies{id,text,username,timestamp}"))


def cmd_reply(args, c):
    msg = read_text(args.text, args.file)
    if not msg:
        raise IGError("Empty reply.")
    if gate(args, f"reply to comment {args.comment_id}", {"message": msg}):
        out(args, c.post(f"{args.comment_id}/replies", message=msg))


def cmd_comment(args, c):
    msg = read_text(args.text, args.file)
    if not msg:
        raise IGError("Empty comment.")
    if gate(args, f"comment on {args.media_id}", {"message": msg}):
        out(args, c.post(f"{args.media_id}/comments", message=msg))


def cmd_hide(args, c):
    if gate(args, f"{'unhide' if args.undo else 'hide'} comment {args.comment_id}", {}):
        out(args, c.post(args.comment_id, hide="false" if args.undo else "true"))


def cmd_delete_comment(args, c):
    if gate(args, f"DELETE comment {args.comment_id}", {}):
        out(args, c.delete(args.comment_id))


def cmd_lookup(args, c):
    """Public stats on another business/creator account (business_discovery)."""
    inner = (f"username,name,biography,followers_count,follows_count,media_count,"
             f"media.limit({args.limit}){{{MEDIA_FIELDS}}}")
    r = c.get(c.user_id, fields=f"business_discovery.username({args.username}){{{inner}}}")
    out(args, r.get("business_discovery", r))


def cmd_limit(args, c):
    out(args, c.get(f"{c.user_id}/content_publishing_limit", fields="config,quota_usage"))


def cmd_status(args, c):
    out(args, c.get(args.container_id, fields="status_code,status"))


def _publish(args, c, container_params: dict, what: str):
    caption = read_text(args.caption, args.caption_file)
    if caption:
        container_params["caption"] = caption
    plan = {"what": what, **container_params}
    if not gate(args, f"publish {what}", plan):
        return
    cid = c.post(f"{c.user_id}/media", **container_params)["id"]
    wait_container(c, cid)
    out(args, c.post(f"{c.user_id}/media_publish", creation_id=cid))


def cmd_post_image(args, c):
    _publish(args, c, {"image_url": need_url(args.url),
                       **({"alt_text": args.alt} if args.alt else {})}, "image")


def cmd_post_reel(args, c):
    p = {"media_type": "REELS", "video_url": need_url(args.url)}
    if args.cover:
        p["cover_url"] = need_url(args.cover)
    if args.no_share_to_feed:
        p["share_to_feed"] = "false"
    _publish(args, c, p, "reel")


def cmd_post_story(args, c):
    p = {"media_type": "STORIES"}
    p["video_url" if args.video else "image_url"] = need_url(args.url)
    p.pop("caption", None)
    if not gate(args, "publish story", p):
        return
    cid = c.post(f"{c.user_id}/media", **p)["id"]
    wait_container(c, cid)
    out(args, c.post(f"{c.user_id}/media_publish", creation_id=cid))


def cmd_post_carousel(args, c):
    if not 2 <= len(args.urls) <= 10:
        raise IGError("A carousel needs 2 to 10 items.")
    urls = [need_url(u) for u in args.urls]
    caption = read_text(args.caption, args.caption_file)
    plan = {"items": urls, "caption": caption}
    if not gate(args, "publish carousel", plan):
        return
    kids = []
    for u in urls:
        video = u.lower().split("?")[0].endswith((".mp4", ".mov"))
        p = {"is_carousel_item": "true", **({"media_type": "VIDEO", "video_url": u} if video else {"image_url": u})}
        kid = c.post(f"{c.user_id}/media", **p)["id"]
        wait_container(c, kid)
        kids.append(kid)
    top = {"media_type": "CAROUSEL", "children": ",".join(kids)}
    if caption:
        top["caption"] = caption
    cid = c.post(f"{c.user_id}/media", **top)["id"]
    wait_container(c, cid)
    out(args, c.post(f"{c.user_id}/media_publish", creation_id=cid))


def cmd_dm_list(args, c):
    out(args, c.paged("me/conversations", args.limit, platform="instagram",
                      fields="id,updated_time,participants,messages.limit(1){message,from,created_time}"))


def cmd_dm_read(args, c):
    out(args, c.paged(f"{args.conversation_id}/messages", args.limit, fields="id,message,from,created_time"))


def cmd_dm_send(args, c):
    msg = read_text(args.text, args.file)
    if not msg:
        raise IGError("Empty message.")
    body = {"recipient": {"id": args.recipient_id}, "message": {"text": msg}}
    if not gate(args, f"DM {args.recipient_id}", {"text": msg}):
        return
    # Messaging endpoint takes JSON.
    req = urllib.request.Request(
        f"{BASE}/{API_VERSION}/{c.user_id}/messages", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {c.token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out(args, json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err = json.loads(raw).get("error", {})
        except ValueError:
            err = {}
        raise IGError(explain(e.code, err, raw)) from None


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ig", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pretty", action="store_true", help="indent JSON output")
    p.add_argument("--yes", "-y", action="store_true", help="actually post/send/delete (default: dry run)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help, needs_client=True):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn, needs_client=needs_client)
        return sp

    s = add("login", cmd_login, "save an access token", needs_client=False)
    s.add_argument("--token", required=True)
    add("refresh", cmd_refresh, "extend a long-lived token by 60 days")
    add("me", cmd_me, "your profile and counts")

    s = add("media", cmd_media_list, "list your recent posts")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--type", help="REELS, FEED, STORY, CAROUSEL_ALBUM, VIDEO, IMAGE")
    s = add("get", cmd_media_get, "one post in detail")
    s.add_argument("id")

    s = add("insights", cmd_insights, "account insights, or one post's with an id")
    s.add_argument("id", nargs="?")
    s.add_argument("--metric", help="comma separated; sensible defaults")
    s.add_argument("--since"), s.add_argument("--until")

    s = add("comments", cmd_comments, "comments under one of your posts")
    s.add_argument("media_id"); s.add_argument("--limit", type=int, default=50)
    for name, fn, hlp, arg in (("reply", cmd_reply, "reply to a comment", "comment_id"),
                               ("comment", cmd_comment, "top-level comment on a media id", "media_id")):
        s = add(name, fn, hlp)
        s.add_argument(arg); s.add_argument("text", nargs="?", help="text, or - for stdin")
        s.add_argument("--file")
    s = add("hide", cmd_hide, "hide a comment on your post")
    s.add_argument("comment_id"); s.add_argument("--undo", action="store_true")
    s = add("delete-comment", cmd_delete_comment, "delete a comment on your post")
    s.add_argument("comment_id")

    s = add("lookup", cmd_lookup, "public stats and recent posts of another business/creator account")
    s.add_argument("username"); s.add_argument("--limit", type=int, default=12)

    add("limit", cmd_limit, "publishing quota used in the last 24h")
    s = add("status", cmd_status, "processing status of an upload container")
    s.add_argument("container_id")

    post = add("post", None, "publish (dry run unless --yes)")
    psub = post.add_subparsers(dest="kind", required=True, metavar="kind")

    def pp(name, fn, help, caption=True):
        sp = psub.add_parser(name, help=help)
        sp.set_defaults(fn=fn, needs_client=True)
        if caption:
            sp.add_argument("--caption"); sp.add_argument("--caption-file")
        return sp
    s = pp("image", cmd_post_image, "single image from a public URL")
    s.add_argument("url"); s.add_argument("--alt")
    s = pp("reel", cmd_post_reel, "reel from a public video URL")
    s.add_argument("url"); s.add_argument("--cover"); s.add_argument("--no-share-to-feed", action="store_true")
    s = pp("carousel", cmd_post_carousel, "2-10 images/videos from public URLs")
    s.add_argument("urls", nargs="+")
    s = pp("story", cmd_post_story, "story from a public URL", caption=False)
    s.add_argument("url"); s.add_argument("--video", action="store_true")

    dm = add("dm", None, "direct messages")
    dsub = dm.add_subparsers(dest="kind", required=True, metavar="action")
    s = dsub.add_parser("list"); s.set_defaults(fn=cmd_dm_list, needs_client=True)
    s.add_argument("--limit", type=int, default=20)
    s = dsub.add_parser("read"); s.set_defaults(fn=cmd_dm_read, needs_client=True)
    s.add_argument("conversation_id"); s.add_argument("--limit", type=int, default=30)
    s = dsub.add_parser("send"); s.set_defaults(fn=cmd_dm_send, needs_client=True)
    s.add_argument("recipient_id"); s.add_argument("text", nargs="?"); s.add_argument("--file")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    flags = {"--yes": False, "-y": False, "--pretty": False}
    for f in flags:  # accept the global flags anywhere on the line
        while f in argv:
            argv.remove(f)
            flags[f] = True
    args = build_parser().parse_args(argv)
    args.yes = args.yes or flags["--yes"] or flags["-y"]
    args.pretty = args.pretty or flags["--pretty"]
    try:
        cfg = load_config()
        client = Client(cfg.get("access_token", ""), cfg.get("user_id")) if args.needs_client else None
        args.fn(args, client)
        return 0
    except IGError as e:
        print(f"ig: {e}", file=sys.stderr)
        return 1
    except (OSError, KeyError) as e:
        print(f"ig: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
