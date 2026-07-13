from __future__ import annotations


_FALL_THROUGH_RISK = {"none": 0, "low": 1, "high": 2}


def _fall_through_risk_not_worse(baseline: dict, scan: dict) -> bool:
    baseline_level = _FALL_THROUGH_RISK.get(
        str(baseline["fall_through_risk"]["level"]).lower()
    )
    scan_level = _FALL_THROUGH_RISK.get(
        str(scan["fall_through_risk"]["level"]).lower()
    )
    return (
        baseline_level is not None
        and scan_level is not None
        and scan_level <= baseline_level
    )


def _ray_scan_not_worse(baseline: dict, scan: dict) -> bool:
    # DeadMesh DOCUMENTATION.md, Ray-Cast pass: fall-through points are listed
    # but not flagged because simplified hulls are normal in Skyrim meshes.
    # We therefore gate only on dmscan's verdict-grade signals: the considered
    # fall-through risk LEVEL and invisible walls (the one ray defect DeadMesh
    # itself flags). Raw fall_patch.sites / holes_enclosed counts are hint
    # metrics with sampling variance ("verify with a drop-test") and reject
    # legitimate simplified hulls, so they are deliberately not gated.
    return (
        _fall_through_risk_not_worse(baseline, scan)
        and scan["invisible_walls"]["count"]
        <= baseline["invisible_walls"]["count"]
    )


def nothing_got_worse(
    baseline: dict,
    scan: dict,
    *,
    ignore: frozenset[str] = frozenset(),
) -> bool:
    baseline_verdict = baseline["verdict"].upper()
    verdict = scan["verdict"].upper()
    if scan["status"] == "BROKEN":
        return False
    for word in ("CRASH", "HANG"):
        if word in verdict:
            return False
    if "HEAVY" not in baseline_verdict and "HEAVY" in verdict:
        return False
    if scan["broken"]["refs"] != 0:
        return False
    if scan["freeze"]["cullVerdict"] > baseline["freeze"]["cullVerdict"]:
        return False
    if "orientation_inverted" not in ignore:
        if scan["orientation"]["inverted"] > baseline["orientation"]["inverted"]:
            return False
    if scan["orientation"]["mixed"] > baseline["orientation"]["mixed"]:
        return False
    if scan["orientation"]["worstTier"] > baseline["orientation"]["worstTier"]:
        return False
    if "winding_inverted" not in ignore:
        if scan["winding_cull"]["inverted"] > baseline["winding_cull"]["inverted"]:
            return False
    if scan["winding_cull"]["ambiguous"] > baseline["winding_cull"]["ambiguous"]:
        return False
    if scan["winding_cull"]["leak"] and not baseline["winding_cull"]["leak"]:
        return False
    if "degenerate" not in ignore:
        if scan["degenerate"]["tris"]["count"] > baseline["degenerate"]["tris"]["count"]:
            return False
    if int(scan["orphan_mopp"]) > int(baseline["orphan_mopp"]):
        return False
    if scan["orphan_collisions"] > baseline["orphan_collisions"]:
        return False
    if baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        if not _ray_scan_not_worse(baseline, scan):
            return False
    return True


def simplify_scan_is_acceptable(baseline: dict, scan: dict) -> bool:
    verdict = scan["verdict"].upper()
    if (
        scan["status"] == "BROKEN"
        or any(word in verdict for word in ("HEAVY", "CRASH", "HANG"))
        or scan["broken"]["refs"] != 0
        or scan["freeze"]["cullVerdict"] >= 1
    ):
        return False
    if scan["orientation"]["inverted"] > baseline["orientation"]["inverted"]:
        return False
    if scan["winding_cull"]["inverted"] > baseline["winding_cull"]["inverted"]:
        return False
    if scan["degenerate"]["tris"]["count"] > baseline["degenerate"]["tris"]["count"]:
        return False
    if baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        if not _ray_scan_not_worse(baseline, scan):
            return False
    return True
