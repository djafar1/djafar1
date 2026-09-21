#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import urllib.error
import urllib.request

API_URL = "https://api.github.com/graphql"
OUT_PATH = pathlib.Path("metrics/private-activity.svg")

QUERY = r"""
query($login: String!, $from: DateTime!, $to: DateTime!, $after: String) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar { totalContributions }
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
    }
    repositories(
      first: 100
      after: $after
      ownerAffiliations: OWNER
      isFork: false
      orderBy: {field: PUSHED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        isPrivate
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node { name color }
          }
        }
      }
    }
  }
}
"""


def graphql(token: str, variables: dict) -> dict:
    payload = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "djafar1-private-metrics",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed: HTTP {exc.code}: {body}") from exc

    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {result['errors']}")
    return result["data"]


def collect(token: str, login: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = now - dt.timedelta(days=365)
    after = None
    contributions = None
    repo_count = 0
    private_repo_count = 0
    language_bytes: dict[str, int] = {}
    language_colors: dict[str, str] = {}

    while True:
        data = graphql(token, {
            "login": login,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
            "after": after,
        })
        user = data.get("user")
        if user is None:
            raise RuntimeError(f"GitHub user {login!r} was not found.")

        if contributions is None:
            contributions = user["contributionsCollection"]

        repos = user["repositories"]
        for repo in repos["nodes"]:
            repo_count += 1
            if repo["isPrivate"]:
                private_repo_count += 1
            for edge in repo["languages"]["edges"]:
                language = edge["node"]["name"]
                language_bytes[language] = language_bytes.get(language, 0) + int(edge["size"])
                if edge["node"].get("color"):
                    language_colors[language] = edge["node"]["color"]

        page = repos["pageInfo"]
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]

    top_languages = sorted(language_bytes.items(), key=lambda x: x[1], reverse=True)[:6]
    return {
        "from": start.date().isoformat(),
        "to": now.date().isoformat(),
        "total": contributions["contributionCalendar"]["totalContributions"],
        "commits": contributions["totalCommitContributions"],
        "pull_requests": contributions["totalPullRequestContributions"],
        "reviews": contributions["totalPullRequestReviewContributions"],
        "issues": contributions["totalIssueContributions"],
        "restricted": contributions["restrictedContributionsCount"],
        "repos": repo_count,
        "private_repos": private_repo_count,
        "languages": [
            {"name": name, "bytes": size, "color": language_colors.get(name, "#8b949e")}
            for name, size in top_languages
        ],
    }


def render(metrics: dict) -> str:
    total_language_bytes = sum(x["bytes"] for x in metrics["languages"]) or 1
    stats = [
        ("Contributions", metrics["total"]),
        ("Commits", metrics["commits"]),
        ("Pull requests", metrics["pull_requests"]),
        ("Code reviews", metrics["reviews"]),
    ]
    xs = [55, 270, 485, 700]
    stat_cells = "".join(
        f'<text x="{x}" y="112" class="value">{html.escape(str(value))}</text>'
        f'<text x="{x}" y="137" class="label">{html.escape(label)}</text>'
        for (label, value), x in zip(stats, xs)
    )

    lang_x = 55
    cursor = lang_x
    segments = []
    labels = []
    for i, item in enumerate(metrics["languages"]):
        frac = item["bytes"] / total_language_bytes
        w = 790 * frac
        if w > 0:
            segments.append(
                f'<rect x="{cursor:.1f}" y="230" width="{w:.1f}" height="12" rx="4" fill="{html.escape(item["color"])}"/>'
            )
        cursor += w

    label_x = 55
    for item in metrics["languages"]:
        pct = 100 * item["bytes"] / total_language_bytes
        labels.append(
            f'<circle cx="{label_x+5}" cy="280" r="5" fill="{html.escape(item["color"])}"/>'
            f'<text x="{label_x+17}" y="285" class="lang">{html.escape(item["name"])} {pct:.0f}%</text>'
        )
        label_x += max(118, 17 + len(item["name"]) * 8 + 45)

    private_text = (
        f'{metrics["private_repos"]} private repositories included in aggregate repository statistics'
        if metrics["private_repos"]
        else "No private repositories were visible to the metrics token"
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="330" viewBox="0 0 900 330" role="img" aria-label="Aggregate GitHub activity">
<style>
.bg {{ fill:#0d1117 }} .border {{ fill:none; stroke:#30363d; stroke-width:1 }}
.title {{ fill:#f0f6fc; font:600 21px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif }}
.subtitle,.label {{ fill:#8b949e; font:13px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif }}
.value {{ fill:#f0f6fc; font:700 26px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif }}
.section {{ fill:#c9d1d9; font:600 14px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif }}
.lang {{ fill:#c9d1d9; font:12px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif }}
</style>
<rect width="100%" height="100%" rx="10" class="bg"/>
<rect x="0.5" y="0.5" width="899" height="329" rx="10" class="border"/>
<text x="55" y="45" class="title">GitHub activity</text>
<text x="55" y="68" class="subtitle">Authenticated aggregate, last 365 days · {metrics["from"]} to {metrics["to"]}</text>
{stat_cells}
<text x="55" y="177" class="section">Repository coverage</text>
<text x="55" y="201" class="subtitle">{metrics["repos"]} owned repositories analyzed · {html.escape(private_text)}</text>
<text x="55" y="221" class="section">Top languages across accessible owned repositories</text>
{''.join(segments)}
{''.join(labels)}
<text x="55" y="315" class="subtitle">Privacy-safe output: no private repository names, file paths, commit messages, or source code are published.</text>
</svg>'''


def main() -> None:
    token = os.environ.get("GH_METRICS_TOKEN")
    login = os.environ.get("GH_LOGIN", "djafar1")
    if not token:
        raise SystemExit("GH_METRICS_TOKEN is missing. Add it as an Actions secret first.")
    metrics = collect(token, login)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render(metrics), encoding="utf-8")
    print(f"Generated {OUT_PATH} using aggregate data from {metrics['repos']} repositories.")

if __name__ == "__main__":
    main()
