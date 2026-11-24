# ig, a CLI for your Instagram account

One Python file, standard library only, on the **official Instagram Graph API**
(Instagram Login). No scraping, no password, no private endpoints, so it will not
get the account flagged.

**Safety rule, same as the skills:** every command that posts, replies, DMs,
hides or deletes is a dry run until you add `--yes`.

## Setup

1. Make the account Business or Creator.
2. In [developers.facebook.com](https://developers.facebook.com) create an app, add
   the **Instagram API with Instagram Login** product, add yourself as a tester,
   and generate a token with `instagram_business_basic`,
   `instagram_business_content_publish`, `instagram_business_manage_comments`,
   `instagram_business_manage_insights`, `instagram_business_manage_messages`.
3. `python3 cli/ig.py login --token <token>` (stored in `~/.config/ig-cli/config.json`, mode 600),
   or just export `IG_ACCESS_TOKEN`. Optional alias: `alias ig='python3 /path/to/cli/ig.py'`.

Long-lived tokens last 60 days; `ig refresh` extends one.

## Commands

```bash
ig me                              # profile + counts
ig media --limit 30 --type REELS   # recent posts
ig get <media_id>
ig insights                        # account, last day
ig insights <media_id>             # one post: reach, likes, shares, saves, views
ig comments <media_id>
ig reply <comment_id> "text"       # dry run
ig reply <comment_id> --file r.txt --yes
ig hide <comment_id> --yes         # --undo to unhide
ig lookup somebrand --limit 12     # public stats of another pro account
ig limit                           # publishing quota (25 posts / 24h)

ig post image    https://cdn.example.com/a.jpg --caption-file cap.txt --yes
ig post reel     https://cdn.example.com/r.mp4 --cover https://.../c.jpg --yes
ig post carousel https://.../1.jpg https://.../2.jpg --caption "..." --yes
ig post story    https://.../s.jpg --yes

ig dm list | ig dm read <conversation_id> | ig dm send <recipient_id> "text" --yes
```

Output is JSON (pipe to `jq`). `--pretty`, `--yes` work anywhere on the line.

## Limits you should know

- **Media must be a public HTTPS URL.** The API fetches it itself; local files can't be
  uploaded. Host the file anywhere (S3, R2, Supabase) first.
- You can only read/manage **your own** account. `lookup` is the one exception and only
  returns public data for other Business/Creator accounts.
- DMs can only reply inside Meta's 24-hour messaging window.
- No API for following, liking others' posts, or commenting from a personal account.

## Tests

`python3 -m unittest discover cli/tests` (runs against a local fake API, no network).
