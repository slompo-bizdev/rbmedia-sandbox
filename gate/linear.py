"""
Linear writer — every finding becomes an issue someone owns.

This is the half of the gate that matters operationally. A report nobody is
assigned to is a report nobody works. The JD for this role asks for vendors
held accountable against SOWs and milestones; this is that, in code.

Idempotency: the finding's stable key goes in the issue title as `[KEY]`.
Before creating, the writer searches the team for that key. Re-running the gate
every morning therefore updates instead of flooding the board.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .rules import BLOCKING, HIGH, Finding

API = "https://api.linear.app/graphql"

# Linear: 1=Urgent, 2=High, 3=Medium, 4=Low
PRIORITY = {BLOCKING: 1, HIGH: 2}
DEFAULT_PRIORITY = 3


class LinearError(RuntimeError):
    pass


def _post(token: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise LinearError(
            f"Linear returned HTTP {e.code}. 400/401 usually means the API key is "
            f"wrong. Body: {e.read()[:400]!r}"
        ) from e
    except urllib.error.URLError as e:
        raise LinearError(
            f"Could not reach {API} ({e.reason}). Run this from a machine with "
            f"plain internet access."
        ) from e
    if payload.get("errors"):
        raise LinearError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


_TEAM = """
query Team($key: String!) {
  teams(filter: { key: { eq: $key } }, first: 1) {
    nodes { id key name }
  }
}
"""

_FIND = """
query Existing($teamId: ID!, $needle: String!) {
  issues(
    filter: { team: { id: { eq: $teamId } }, title: { contains: $needle } }
    first: 1
  ) { nodes { id identifier title url state { name type } } }
}
"""

_CREATE = """
mutation Create($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""

_UPDATE = """
mutation Update($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { id identifier url } }
}
"""

_LABEL = """
mutation Label($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) { success issueLabel { id name } }
}
"""

_LABELS = """
query Labels($teamId: ID!) {
  issueLabels(filter: { team: { id: { eq: $teamId } } }, first: 250) {
    nodes { id name }
  }
}
"""


def _resolve_team(token: str, key: str) -> str:
    nodes = _post(token, _TEAM, {"key": key})["teams"]["nodes"]
    if not nodes:
        raise LinearError(
            f"No team with key {key!r} in this workspace. The key is the short "
            f"prefix on issue ids, e.g. RBM in RBM-12."
        )
    return nodes[0]["id"]


def _ensure_labels(token: str, team_id: str, names: set[str]) -> dict[str, str]:
    existing = {
        n["name"]: n["id"] for n in _post(token, _LABELS, {"teamId": team_id})["issueLabels"]["nodes"]
    }
    for name in sorted(names - existing.keys()):
        data = _post(token, _LABEL, {"input": {"name": name, "teamId": team_id}})
        if data["issueLabelCreate"]["success"]:
            existing[name] = data["issueLabelCreate"]["issueLabel"]["id"]
    return existing


def _body(f: Finding) -> str:
    money = (
        f"**Revenue at risk:** ${f.revenue_at_risk_usd:,.2f} / month\n"
        if f.revenue_at_risk_usd
        else ""
    )
    evidence = "\n".join(f"- `{k}`: {v}" for k, v in sorted(f.evidence.items()))
    return (
        f"{f.detail}\n\n"
        f"{money}"
        f"**ISBN:** `{f.isbn}`  ·  **Title:** {f.title}  ·  **Owner:** `{f.owner}`\n\n"
        f"**Evidence**\n{evidence or '- none'}\n\n"
        f"---\n"
        f"Opened by the catalogue integrity gate. Re-running the gate updates this "
        f"issue rather than creating a new one."
    )


def sync(findings: list[Finding], team_key: str, token: str | None = None) -> list[dict]:
    """Create or update one issue per finding. Returns what it did, per finding."""
    token = token or os.environ.get("LINEAR_API_KEY", "")
    if not token:
        raise LinearError("Set LINEAR_API_KEY.")

    team_id = _resolve_team(token, team_key)
    labels = _ensure_labels(
        token,
        team_id,
        {f.rule for f in findings} | {f"owner:{f.owner}" for f in findings if f.owner},
    )

    results = []
    for f in findings:
        title = f"[{f.key}] {f.title or f.isbn}"
        found = _post(token, _FIND, {"teamId": team_id, "needle": f.key})["issues"]["nodes"]
        label_ids = [
            labels[n] for n in (f.rule, f"owner:{f.owner}") if n in labels
        ]
        if found:
            issue = found[0]
            data = _post(
                token,
                _UPDATE,
                {"id": issue["id"], "input": {"description": _body(f), "labelIds": label_ids}},
            )["issueUpdate"]
            results.append(
                {"key": f.key, "action": "updated", "identifier": issue["identifier"],
                 "url": data["issue"]["url"]}
            )
            continue

        data = _post(
            token,
            _CREATE,
            {
                "input": {
                    "teamId": team_id,
                    "title": title[:250],
                    "description": _body(f),
                    "priority": PRIORITY.get(f.severity, DEFAULT_PRIORITY),
                    "labelIds": label_ids,
                }
            },
        )["issueCreate"]
        results.append(
            {"key": f.key, "action": "created",
             "identifier": data["issue"]["identifier"], "url": data["issue"]["url"]}
        )
    return results
