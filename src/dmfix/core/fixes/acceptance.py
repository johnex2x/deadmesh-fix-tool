from __future__ import annotations


_FALL_THROUGH_RISK = {"none": 0, "low": 1, "high": 2}


def _section(record: dict, key: str) -> dict:
    """dmscan emits null for analysis sections it did not run on a mesh."""
    return record.get(key) or {}


def _fall_through_risk_not_worse(baseline: dict, scan: dict) -> bool:
    baseline_level = _FALL_THROUGH_RISK.get(
        str(_section(baseline, "fall_through_risk").get("level", "")).lower()
    )
    scan_level = _FALL_THROUGH_RISK.get(
        str(_section(scan, "fall_through_risk").get("level", "")).lower()
    )
    if scan_level is None:
        # Post-fix level unmeasured/unknown: acceptable only if the baseline
        # was equally unmeasured; otherwise fail closed.
        return baseline_level is None
    return scan_level <= (baseline_level if baseline_level is not None else 0)


def _metric(record: dict, *keys: str, default: int = 0) -> int:
    """Nested dmscan metric with null-section tolerance (missing = 0 findings)."""
    value: object = record
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    if value is None:
        return default
    return int(value)


def _ray_scan_not_worse(baseline: dict, scan: dict) -> bool:
    # DeadMesh DOCUMENTATION.md, Ray-Cast pass: fall-through points are listed
    # but not flagged because simplified hulls are normal in Skyrim meshes.
    # We therefore gate only on dmscan's verdict-grade signals: the considered
    # fall-through risk LEVEL and invisible walls (the one ray defect DeadMesh
    # itself flags). Raw fall_patch.sites / holes_enclosed counts are hint
    # metrics with sampling variance ("verify with a drop-test") and reject
    # legitimate simplified hulls, so they are deliberately not gated.
    return _fall_through_risk_not_worse(baseline, scan) and _metric(
        scan, "invisible_walls", "count"
    ) <= _metric(baseline, "invisible_walls", "count")


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
    if _metric(scan, "broken", "refs") != 0:
        return False
    if _metric(scan, "freeze", "cullVerdict") > _metric(baseline, "freeze", "cullVerdict"):
        return False
    if "orientation_inverted" not in ignore:
        if _metric(scan, "orientation", "inverted") > _metric(baseline, "orientation", "inverted"):
            return False
    if _metric(scan, "orientation", "mixed") > _metric(baseline, "orientation", "mixed"):
        return False
    if _metric(scan, "orientation", "worstTier") > _metric(baseline, "orientation", "worstTier"):
        return False
    if "winding_inverted" not in ignore:
        if _metric(scan, "winding_cull", "inverted") > _metric(baseline, "winding_cull", "inverted"):
            return False
    if _metric(scan, "winding_cull", "ambiguous") > _metric(baseline, "winding_cull", "ambiguous"):
        return False
    if bool(_section(scan, "winding_cull").get("leak")) and not bool(
        _section(baseline, "winding_cull").get("leak")
    ):
        return False
    if "degenerate" not in ignore:
        if _metric(scan, "degenerate", "tris", "count") > _metric(
            baseline, "degenerate", "tris", "count"
        ):
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
    return not simplify_certification_failures(baseline, scan)


def simplify_certification_failures(baseline: dict, scan: dict) -> list[str]:
    """Return the exact safety-gate checks that rejected a candidate.

    This deliberately mirrors :func:`simplify_scan_is_acceptable` so the GUI
    can explain a failed rescue without changing the conservative gate.
    """
    failures: list[str] = []
    verdict = str(scan.get("verdict", "")).upper()
    status = str(scan.get("status", ""))
    if status == "BROKEN":
        failures.append("status=BROKEN")
    for word in ("HEAVY", "CRASH", "HANG"):
        if word in verdict:
            failures.append(f"verdict contains {word}")
            break
    broken_refs = _metric(scan, "broken", "refs")
    if broken_refs != 0:
        failures.append(f"broken.refs={broken_refs}")
    cull = _metric(scan, "freeze", "cullVerdict")
    if cull >= 1:
        failures.append(f"freeze.cullVerdict={cull}")
    orientation = _metric(scan, "orientation", "inverted")
    baseline_orientation = _metric(baseline, "orientation", "inverted")
    if orientation > baseline_orientation:
        failures.append(f"orientation.inverted={orientation}>{baseline_orientation}")
    winding = _metric(scan, "winding_cull", "inverted")
    baseline_winding = _metric(baseline, "winding_cull", "inverted")
    if winding > baseline_winding:
        failures.append(f"winding_cull.inverted={winding}>{baseline_winding}")
    degenerate = _metric(scan, "degenerate", "tris", "count")
    baseline_degenerate = _metric(baseline, "degenerate", "tris", "count")
    if degenerate > baseline_degenerate:
        failures.append(f"degenerate.tris.count={degenerate}>{baseline_degenerate}")
    if baseline.get("ray_status") == "ok" and scan.get("ray_status") == "ok":
        baseline_risk = str(_section(baseline, "fall_through_risk").get("level", "unknown"))
        risk = str(_section(scan, "fall_through_risk").get("level", "unknown"))
        if not _fall_through_risk_not_worse(baseline, scan):
            failures.append(f"fall_through_risk.level={risk}>{baseline_risk}")
        invisible = _metric(scan, "invisible_walls", "count")
        baseline_invisible = _metric(baseline, "invisible_walls", "count")
        if invisible > baseline_invisible:
            failures.append(f"invisible_walls.count={invisible}>{baseline_invisible}")
    return failures
