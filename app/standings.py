"""Compute standings: ranks, checkpoints, movement, jerseys, team totals.

Mirrors the HTML recompute() + jersey selection (handoff §5, §6). Operates on
plain rider dicts (as produced by ingest) and returns typed models.
"""
from typing import Optional, Callable

from . import constants
from .models import Rider, TeamStanding, Jersey, Jerseys


# ---------- per-rider metric helpers (HTML raceVal / cpsOf / quality) -------
def race_val(a: dict, metric: str) -> int:
    """Value the race ranks by: total sales, or % of target (capped 0+)."""
    if metric == "target":
        return max(0, round((a.get("target") or 0) * 100))
    return int(a.get("ld") or 0)


def race_unit(metric: str) -> str:
    return "% of target" if metric == "target" else "sales"


def quality(a: dict) -> Optional[float]:
    """Lead quality 0-100: validpct, else valid/ld×100, else conv×100, else None."""
    vp = a.get("validpct")
    if vp is not None:
        return vp
    v = a.get("valid")
    ld = a.get("ld") or 0
    if v is not None and ld > 0:
        return v / ld * 100
    c = a.get("conv")
    if c is not None:
        return c * 100
    return None


def _top_by(riders: list[dict], sel: Callable[[dict], float]) -> Optional[dict]:
    """Rider with the highest positive sel(...), else None (HTML topBy)."""
    best = None
    for a in riders:
        v = sel(a) or 0
        if v > 0 and (best is None or v > (sel(best) or 0)):
            best = a
    return best


def _jersey(a: Optional[dict], stat: str) -> Optional[Jersey]:
    if not a:
        return None
    return Jersey(id=a["id"], n=a["n"], stat=stat)


# ---------- main compute -----------------------------------------------------
def compute_standings(
    riders: list[dict],
    *,
    race_metric: str = "sales",
    prev_rank: Optional[dict] = None,
    prev_sales: Optional[dict] = None,
    week_baseline: Optional[dict] = None,
    streaks: Optional[dict] = None,
) -> dict:
    """Return {riders:[Rider], teams:[TeamStanding], jerseys:Jerseys, order:[id]}.

    prev_rank / prev_sales: {id -> value} snapshot from the previous upload
        (for dRank/dSales). Empty/None on the first upload -> movement is 0.
    week_baseline: {id -> cumulative ld at Monday} for the weekly stage score.
    streaks: {id -> consecutive-days-with-+5} computed by the storage layer.
    """
    prev_rank = prev_rank or {}
    prev_sales = prev_sales or {}
    streaks = streaks or {}

    # rank order: race value desc, then sales/day desc, then name (HTML byLeads)
    by_leads = sorted(
        riders,
        key=lambda a: (-race_val(a, race_metric), -(a.get("r") or 0), a.get("n", "")),
    )

    # derive per-rider fields
    for i, a in enumerate(by_leads):
        rid = a["id"]
        rv = race_val(a, race_metric)
        a["rank"] = i + 1
        a["dRank"] = (prev_rank[rid] - a["rank"]) if rid in prev_rank else 0
        a["dSales"] = (int(a.get("ld") or 0) - prev_sales[rid]) if rid in prev_sales else 0
        a["cps"] = constants.checkpoints_for(rv)
        if week_baseline is not None:
            a["week"] = max(0, int(a.get("ld") or 0) - int(week_baseline.get(rid, 0)))
        else:
            a["week"] = 0
        a["streak"] = int(streaks.get(rid, 0))

    # --- jerseys (HTML selection) ---
    yellow = by_leads[0] if by_leads else None
    green = next(
        (a for a in sorted(riders, key=lambda x: (-(x.get("r") or 0), -(x.get("ld") or 0)))
         if not yellow or a["id"] != yellow["id"]),
        None,
    )
    polka = max(riders, key=lambda x: x.get("pp") or 0) if riders else None
    rookies = [a for a in riders if constants.tenure_info(a.get("tn")).get("rookie")]
    white = sorted(rookies, key=lambda x: (-(x.get("ld") or 0), -(x.get("r") or 0)))[0] if rookies else None
    sprint = _top_by(riders, lambda x: x.get("dSales") or 0)
    breakaway = _top_by(riders, lambda x: x.get("dRank") or 0)
    mva_k = _top_by(riders, lambda x: x.get("mva") or 0)
    wc_k = _top_by(riders, lambda x: x.get("wc") or 0)
    pi_k = _top_by(riders, lambda x: x.get("pi") or 0)

    # clean rider: highest quality among ld>=10 (fallback: all with quality)
    cq = [a for a in riders if quality(a) is not None and (a.get("ld") or 0) >= 10]
    if not cq:
        cq = [a for a in riders if quality(a) is not None]
    clean = sorted(cq, key=lambda x: (-(quality(x) or 0), -(x.get("ld") or 0)))[0] if cq else None

    jerseys = Jerseys(
        yellow=_jersey(yellow, f"{int(yellow['ld'])} sales") if yellow else None,
        green=_jersey(green, f"{green['r']:.2f} sales/day") if green else None,
        polka=_jersey(polka, f"{(polka.get('pp') or 0):.0f}% productive") if polka else None,
        white=_jersey(white, f"{int(white['ld'])} sales") if white else None,
        sprint=_jersey(sprint, f"+{sprint['dSales']} since last update") if (sprint and sprint.get("dSales", 0) > 0) else None,
        breakaway=_jersey(breakaway, f"▲{breakaway['dRank']} places") if (breakaway and breakaway.get("dRank", 0) > 0) else None,
        mva=_jersey(mva_k, f"{mva_k['mva']} MVA leads") if mva_k else None,
        wc=_jersey(wc_k, f"{wc_k['wc']} WC leads") if wc_k else None,
        pi=_jersey(pi_k, f"{pi_k['pi']} PI leads") if pi_k else None,
        clean=_jersey(clean, f"{quality(clean):.0f}% valid · {int(clean['ld'])} sales") if clean else None,
    )

    # --- team totals + colours (by total sales desc) ---
    sums, counts = {}, {}
    for a in riders:
        t = a.get("tl") or "Unassigned"
        sums[t] = sums.get(t, 0) + int(a.get("ld") or 0)
        counts[t] = counts.get(t, 0) + 1
    colors = constants.assign_team_colors(sums)
    teams = [
        TeamStanding(team=t, total=sums[t], count=counts[t],
                     avg=round(sums[t] / counts[t], 1), color=colors[t])
        for t in sorted(sums, key=lambda x: -sums[x])
    ]

    rider_models = [Rider(**a) for a in by_leads]
    return {
        "riders": rider_models,
        "teams": teams,
        "jerseys": jerseys,
        "order": [a["id"] for a in by_leads],
    }


def snapshot_ranks(result_riders: list[Rider]) -> tuple[dict, dict]:
    """Snapshot {id->rank} and {id->ld} for movement on the *next* upload (§6)."""
    return (
        {a.id: a.rank for a in result_riders},
        {a.id: a.ld for a in result_riders},
    )
