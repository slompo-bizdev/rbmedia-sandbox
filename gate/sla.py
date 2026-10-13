"""
Ageing and escalation — the part that gives vendor accountability teeth.

A board full of open tickets is not accountability; it is a list. What makes it
accountability is that a ticket owned by a vendor has a clock on it, and that
the clock being blown is itself visible.

There is no state file here on purpose. **The Linear issue is the state.** Its
`createdAt` is when we first saw the problem, its state tells us whether anyone
closed it, and the `sla-breach` label is the marker that stops us escalating the
same thing twice. One source of truth, and it is the one the humans look at.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .linear import _ensure_labels, _post, _resolve_team, LinearError

# Business days are the honest unit here, but calendar days are what a vendor
# argues about, so calendar days it is. These would come out of the SOW.
SLA_DAYS = {
    "rights-ops": 2,          # a live breach: same-week is not good enough
    "fulfilment-vendor": 3,
    "pricing-vendor": 5,
    "catalogue-vendor": 5,
    "platform-vendor": 5,
    "client-product": 7,      # the client answering a question about their own business
}
DEFAULT_SLA_DAYS = 5

BREACH_LABEL = "sla-breach"

_OPEN_ISSUES = """
query Open($teamId: ID!, $cursor: String) {
  issues(
    filter: { team: { id: { eq: $teamId } }, state: { type: { nin: ["completed", "canceled"] } } }
    first: 100
    after: $cursor
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title url createdAt priority
      state { name type }
      labels { nodes { name } }
    }
  }
}
"""

_COMMENT = """
mutation Comment($input: CommentCreateInput!) {
  commentCreate(input: $input) { success }
}
"""


@dataclass
class Breach:
    identifier: str
    url: str
    title: str
    owner: str
    age_days: int
    sla_days: int
    escalated_now: bool

    @property
    def over_by(self) -> int:
        return self.age_days - self.sla_days


def _owner_of(labels: list[str]) -> str:
    for name in labels:
        if name.startswith("owner:"):
            return name.split(":", 1)[1]
    return ""


def _age_days(created_at: str, now: datetime) -> int:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (now - created).days


def check_slas(team_key: str, token: str | None = None, now: datetime | None = None,
               escalate: bool = True) -> list[Breach]:
    """Find open issues past their owner's SLA and escalate the new ones."""
    token = token or os.environ.get("LINEAR_API_KEY", "")
    if not token:
        raise LinearError("Set LINEAR_API_KEY.")
    now = now or datetime.now(timezone.utc)

    team_id = _resolve_team(token, team_key)

    nodes, cursor = [], None
    while True:
        page = _post(token, _OPEN_ISSUES, {"teamId": team_id, "cursor": cursor})["issues"]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    breaches: list[Breach] = []
    to_mark: list[dict] = []

    for n in nodes:
        # Only issues this gate opened carry a [RULE:...] key in the title.
        if "[R" not in n["title"]:
            continue
        labels = [l["name"] for l in n["labels"]["nodes"]]
        owner = _owner_of(labels)
        sla = SLA_DAYS.get(owner, DEFAULT_SLA_DAYS)
        age = _age_days(n["createdAt"], now)
        if age <= sla:
            continue
        already = BREACH_LABEL in labels
        breaches.append(
            Breach(
                identifier=n["identifier"],
                url=n["url"],
                title=n["title"].split(" [")[0],
                owner=owner or "unassigned",
                age_days=age,
                sla_days=sla,
                escalated_now=not already,
            )
        )
        if not already:
            to_mark.append({"id": n["id"], "labels": labels, "owner": owner, "age": age, "sla": sla})

    if escalate and to_mark:
        label_ids = _ensure_labels(token, team_id, {BREACH_LABEL})
        from .linear import _UPDATE

        for item in to_mark:
            _post(
                token,
                _UPDATE,
                {
                    "id": item["id"],
                    "input": {
                        "priority": 1,  # Urgent
                        "labelIds": sorted(
                            {label_ids[BREACH_LABEL]}
                            | {
                                lid
                                for lid in _ensure_labels(
                                    token, team_id, set(item["labels"])
                                ).values()
                            }
                        ),
                    },
                },
            )
            _post(
                token,
                _COMMENT,
                {
                    "input": {
                        "issueId": item["id"],
                        "body": (
                            f"**SLA breach.** Open {item['age']} days against a "
                            f"{item['sla']}-day SLA for `{item['owner'] or 'unassigned'}`. "
                            f"Raised to Urgent automatically; this comment is written once."
                        ),
                    }
                },
            )

    breaches.sort(key=lambda b: -b.over_by)
    return breaches
