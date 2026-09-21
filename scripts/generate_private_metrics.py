#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
OUT_PATH = pathlib.Path("metrics/private-activity.svg")


def api_request(token: str, url: str) -> tuple[object, dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "djafar1-private-metrics",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
            headers = {k.lower(): v for k, v in response.headers.items()}
            return data, headers
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return [], {}
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed: HTTP {exc.code}: {body}"
        ) from exc


def list_owned_repositories(token: str, login: str) -> list[dict]:
    repos: list[dict] = []
    page = 1

    while True:
        params = urllib.parse.urlencode(
            {
                "affiliation": "owner",
                "visibility": "all",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            }
        )
        data, _ = api_request(token, f"{API}/user/repos?{params}")

        if not isinstance(data, list):
            raise RuntimeError("Unexpected response while listing repositories.")

        repos.extend(
            repo
            for repo in data
            if repo.get("owner", {}).get("login", "").lower() == login.lower()
            and not repo.get("fork", False)
        )

        if len(data) < 100:
            break
        page += 1

    return repos


def count_commits(
    token: str,
    full_name: str,
    login: str,
    since: str,
    until: str,
) -> int:
    params = urllib.parse.urlencode(
        {
            "author": login,
            "since": since,
            "until": until,
            "per_page": 1,
        }
    )
    data, headers = api_request(
        token,
        f"{API}/repos/{full_name}/commits?{params}",
    )

    if not isinstance(data, list) or not data:
        return 0

    link = headers.get("link", "")
    for part in link.split(","):
        if 'rel="last"' in part:
            match = re.search(r"[?&]page=(\d+)", part)
            if match:
                return int(match.group(1))

    return len(data)



def collect(token: str, login: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = now - dt.timedelta(days=365)

    since = start.isoformat().replace("+00:00", "Z")
    until = now.isoformat().replace("+00:00", "Z")
    repos = list_owned_repositories(token, login)

    total_commits = 0
    active_repositories = 0
    private_repositories = 0

    for repo in repos:
        if repo.get("private"):
            private_repositories += 1

        commit_count = count_commits(
            token=token,
            full_name=repo["full_name"],
            login=login,
            since=since,
            until=until,
        )

        total_commits += commit_count

        if commit_count > 0:
            active_repositories += 1

    return {
        "from": start.date().isoformat(),
        "to": now.date().isoformat(),
        "commits": total_commits,
        "active_repositories": active_repositories,
        "repositories": len(repos),
        "private_repositories": private_repositories,
    }


def render(metrics: dict) -> str:
    stats = [
        ("Commits", metrics["commits"]),
        ("Active repos", metrics["active_repositories"]),
        ("Owned repos", metrics["repositories"]),
        ("Private repos", metrics["private_repositories"]),
    ]

    x_positions = [65, 285, 505, 725]

    stat_cells = "".join(
        f'<text x="{x}" y="137" class="value">{html.escape(str(value))}</text>'
        f'<text x="{x}" y="163" class="label">{html.escape(label)}</text>'
        for (label, value), x in zip(stats, x_positions)
    )

    coverage = (
        f'{metrics["repositories"]} owned repositories analyzed, '
        f'{metrics["private_repositories"]} private'
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg"
width="900" height="255" viewBox="0 0 900 255"
role="img" aria-label="Aggregate GitHub activity">

<style>
.bg {{
  fill: #0d1117;
}}
.border {{
  fill: none;
  stroke: #30363d;
  stroke-width: 1;
}}
.title {{
  fill: #f0f6fc;
  font: 600 22px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;
}}
.subtitle {{
  fill: #8b949e;
  font: 13px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;
}}
.value {{
  fill: #f0f6fc;
  font: 700 30px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;
}}
.label {{
  fill: #8b949e;
  font: 13px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;
}}
.note {{
  fill: #c9d1d9;
  font: 13px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;
}}
</style>

<rect width="100%" height="100%" rx="10" class="bg"/>
<rect x="0.5" y="0.5" width="899" height="254" rx="10" class="border"/>

<text x="55" y="47" class="title">GitHub Activity</text>

<text x="55" y="72" class="subtitle">
Public + private development, last 365 days · {metrics["from"]} to {metrics["to"]}
</text>

<line x1="55" y1="91" x2="845" y2="91" stroke="#30363d" stroke-width="1"/>

{stat_cells}

<text x="55" y="205" class="note">
{html.escape(coverage)}
</text>

<text x="55" y="229" class="subtitle">
Commits are authored commits on default branches. Private repository names and contents are never published.
</text>

</svg>'''


def main() -> None:
    token = os.environ.get("GH_METRICS_TOKEN")
    login = os.environ.get("GH_LOGIN", "djafar1")

    if not token:
        raise SystemExit(
            "GH_METRICS_TOKEN is missing. Add it as a repository Actions secret."
        )

    metrics = collect(token, login)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render(metrics), encoding="utf-8")

    print(
        f"Generated {OUT_PATH}: "
        f"{metrics['commits']} commits across "
        f"{metrics['active_repositories']} active repositories."
    )


if __name__ == "__main__":
    main()
