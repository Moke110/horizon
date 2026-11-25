"""Convergence states that originate in the Dictionary and Hpedia."""

from hostrun.agent_service import CommandError


def validate(value):
    if value not in ("resolved", "unresolved", "conflicted"):
        raise CommandError("invalid_convergence", "收敛状态无效")
    return value


def current(record):
    return record.get("convergence") or (
        "unresolved" if record.get("questionable") else "resolved")
