"""Convergence states that originate in the Dictionary and Hpedia."""

from hostrun.agent_service import CommandError


def validate(value):
    if value not in ("resolved", "unresolved", "conflicted"):
        raise CommandError("invalid_convergence", "收敛状态无效")
    return value


def current(record):
    return record.get("convergence") or (
        "unresolved" if record.get("questionable") else "resolved")


def compatibility_state(record):
    if record["validity"] == "questionable":
        return "conflicted"
    return "unresolved" if record["tracked"] else "resolved"


def attributes(record, changes=None):
    """Project old records or apply an edit without changing lifecycle state."""
    changes = changes or {}
    state = record.get("convergence")
    validity = record.get("validity", "questionable" if (
        state == "conflicted" or not state and record.get("questionable")) else "valid")
    tracked = record.get("tracked", state == "unresolved" or validity == "questionable")
    if "convergence" in changes:
        state = validate(changes["convergence"])
        validity, tracked = ("questionable" if state == "conflicted" else "valid"), state != "resolved"
    elif "questionable" in changes:
        validity = "questionable" if changes["questionable"] else "valid"
        tracked = changes["questionable"]
    validity = changes.get("validity", validity)
    tracked = changes.get("tracked", tracked)
    if validity not in ("valid", "questionable") or type(tracked) is not bool:
        raise CommandError("invalid_state", "有效性或追踪选择无效")
    return {"validity": validity, "tracked": tracked or validity == "questionable"}
