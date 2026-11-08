"""Offline checks for the distribution-package documentation switch (#99).

These tests read the user-facing documents and the runtime skill reference and
compare them with the CLI the code actually ships.  They never run an install
and never touch the network: the point is that the documented path, the command
names and the release conventions stay truthful as the code moves.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
AGENTS = ROOT / "AGENTS.md"
ACCEPTANCE = ROOT / "docs" / "acceptance" / "README.md"
ADR = ROOT / "docs" / "adr" / "0010-distribution-packages-for-installation.md"
SKILL = ROOT / "skills" / "horizon-run" / "SKILL.md"
INSTALLATION = ROOT / "skills" / "horizon-run" / "references" / "installation.md"
WORKFLOW = ROOT / "skills" / "horizon-run" / "references" / "workflow.md"

POINTER_LABELS = (
    "发行来源",
    "发行版本",
    "发行渠道",
    "制品摘要",
    "发行包来源",
    "源码提交",
    "工作区改动",
    "源码来源",
    "技能摘要",
    "安装锁",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, title: str) -> str:
    """Return the body of the first ``## `` section whose heading contains title."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("## ") and title in line:
            start = index + 1
            break
    assert start is not None, f"missing section {title!r}"
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def _walk_subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # argparse has no public walker
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _subcommand_parser(*path: str) -> argparse.ArgumentParser:
    from hostrun.cli import build_parser

    parser = build_parser()
    for part in path:
        choices = _walk_subparsers(parser)
        assert part in choices, f"CLI does not accept {part!r} after {path!r}"
        parser = choices[part]
    return parser


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    options: set[str] = set()
    for action in parser._actions:
        options.update(action.option_strings)
    return options


def _code_spans(text: str) -> list[str]:
    return [span.strip() for span in re.findall(r"`([^`\n]+)`", text)]


_INSTALL_CHOICES = {
    "check",
    "status",
    "report",
    "migrate",
    "record",
    "package",
    "verify",
    "rollback",
    "workspace",
    "toolchain",
    "skill",
    "locate",
}
_CONSOLE_CHOICES = {"control", "install"}
_CONTROL_ACTIONS = {"start", "stop", "status", "push"}


def _assert_commands_exist(text: str, label: str) -> None:
    for span in _code_spans(text):
        for match in re.finditer(r"\binstall\s+([a-z][a-z-]*)", span):
            name = match.group(1)
            assert name in _INSTALL_CHOICES, f"{label}: unknown install command {name!r} in `{span}`"
            _subcommand_parser("install", name)
        for match in re.finditer(r"\bconsole\s+([a-z][a-z-]*)", span):
            name = match.group(1)
            assert name in _CONSOLE_CHOICES, f"{label}: unknown console command {name!r} in `{span}`"
            parser = _subcommand_parser("console", name)
            if name == "control":
                action = re.search(r"\bconsole\s+control\s+([a-z][a-z-]*)", span)
                if action:
                    assert action.group(1) in _CONTROL_ACTIONS, (
                        f"{label}: unknown console control action {action.group(1)!r} in `{span}`"
                    )


_USER_PATH_CASES = (
    # (path, install section heading, package marker, "no toolchain" claim)
    (README, "Installation", "distribution package", "no git"),
    (README_ZH, "安装", "发行包", "不需要 git"),
)


def test_documented_commands_match_the_cli():
    for path in (README, README_ZH, INSTALLATION, SKILL):
        _assert_commands_exist(_read(path), path.name)


def test_console_install_no_longer_requires_an_external_agent():
    options = _option_strings(_subcommand_parser("console", "install"))
    assert "--dsh" not in options
    assert "--dsh-arg" not in options
    assert {"--from", "--digest"} <= _option_strings(_subcommand_parser("install", "package"))


def test_user_docs_explain_model_configuration_and_bundled_runtime():
    for path in (README, README_ZH):
        text = _read(path)
        assert "Pi" in text and "API Key" in text or "API key" in text
        assert "agent/model.json" in text
        assert "console install --dsh" not in text
