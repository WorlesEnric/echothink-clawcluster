from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Generic, Sequence, TypeVar

from src.models.work_item import ApprovalPolicy, RiskLevel, WorkItemCreate, WorkItemKind

PayloadT = TypeVar("PayloadT")


class PayloadNormalizationError(ValueError):
    pass


class BaseHandler(ABC, Generic[PayloadT]):
    def __init__(self, *, cluster_name: str, domain: str) -> None:
        self.cluster_name = cluster_name
        self.domain = domain

    @abstractmethod
    async def to_work_item(self, payload: PayloadT) -> WorkItemCreate:
        raise NotImplementedError

    def normalize_text(self, text: str | None) -> str:
        if not text:
            return ""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def classify_kind(
        self,
        title: str,
        body: str,
        *,
        source_type: str,
        labels: Sequence[str] | None = None,
    ) -> WorkItemKind:
        label_text = " ".join(labels or [])
        corpus = f"{title}\n{body}\n{label_text}".lower()

        if source_type == "gitlab_mr":
            return WorkItemKind.code_review

        keyword_map: tuple[tuple[WorkItemKind, tuple[str, ...]], ...] = (
            (WorkItemKind.workflow_author, ("workflow", "automation", "n8n", "dify", "pipeline")),
            (WorkItemKind.qa_validate, ("qa", "validate", "verification", "regression", "test plan")),
            (WorkItemKind.plan_breakdown, ("breakdown", "roadmap", "milestone", "plan", "decompose")),
            (WorkItemKind.plan_status, ("status update", "weekly update", "progress update", "status")),
            (WorkItemKind.plan_support, ("support", "coordination", "triage", "follow-up")),
            (WorkItemKind.knowledge_sync, ("knowledge", "runbook", "playbook", "documentation", "docs", "wiki")),
            (WorkItemKind.code_review, ("review", "approve", "peer review", "merge request", "pull request")),
        )

        for kind, keywords in keyword_map:
            if any(keyword in corpus for keyword in keywords):
                return kind

        return WorkItemKind.code_implement

    def extract_objective(self, title: str, body: str) -> str:
        explicit_objective = self._extract_named_section(body, {"objective", "goal", "summary", "task"})
        if explicit_objective:
            return explicit_objective

        first_paragraph = self._first_paragraph(body)
        if first_paragraph:
            return first_paragraph

        title_text = title.strip()
        if not title_text:
            raise PayloadNormalizationError("Unable to derive work item objective from payload")
        return title_text

    def extract_acceptance_criteria(self, body: str) -> list[str]:
        section_text = self._extract_named_section(
            body,
            {"acceptance criteria", "done when", "requirements", "checklist"},
        )
        extracted = self._extract_list_items(section_text or "")
        if extracted:
            return extracted

        checklist_items = self._extract_list_items(body, require_checkbox=True)
        if checklist_items:
            return checklist_items

        sentence_matches = []
        for sentence in re.split(r"(?<=[.!?])\s+", body):
            normalized = sentence.strip()
            lowered = normalized.lower()
            if normalized and any(token in lowered for token in ("must ", "should ", "need to ", "needs to ")):
                sentence_matches.append(normalized)
        if sentence_matches:
            return sentence_matches[:5]

        return ["Deliver the requested outcome described in the objective."]

    def determine_priority(
        self,
        title: str,
        body: str,
        *,
        labels: Sequence[str] | None = None,
        default: int = 50,
    ) -> int:
        corpus = f"{title}\n{body}\n{' '.join(labels or [])}".lower()
        if any(keyword in corpus for keyword in ("critical", "sev1", "p0", "blocker", "urgent", "asap")):
            return 90
        if any(keyword in corpus for keyword in ("high priority", "sev2", "p1", "production", "customer")):
            return 75
        if any(keyword in corpus for keyword in ("low priority", "nice to have", "backlog", "when possible")):
            return 35
        return default

    def determine_risk(
        self,
        title: str,
        body: str,
        *,
        labels: Sequence[str] | None = None,
    ) -> RiskLevel:
        corpus = f"{title}\n{body}\n{' '.join(labels or [])}".lower()
        if any(keyword in corpus for keyword in ("sev1", "critical", "security", "incident", "outage", "data loss")):
            return RiskLevel.critical
        if any(keyword in corpus for keyword in ("auth", "billing", "payments", "production", "migration", "high risk")):
            return RiskLevel.high
        if any(keyword in corpus for keyword in ("docs", "documentation", "typo", "status update", "knowledge")):
            return RiskLevel.low
        return RiskLevel.medium

    def determine_approval_policy(
        self,
        risk_level: RiskLevel,
        kind: WorkItemKind,
    ) -> ApprovalPolicy:
        if risk_level is RiskLevel.critical:
            return ApprovalPolicy.critical
        if risk_level is RiskLevel.high:
            return ApprovalPolicy.high
        if kind in {WorkItemKind.plan_status, WorkItemKind.knowledge_sync} and risk_level is RiskLevel.low:
            return ApprovalPolicy.low
        if kind is WorkItemKind.plan_support and risk_level is RiskLevel.low:
            return ApprovalPolicy.none
        return ApprovalPolicy.medium

    def build_requested_by(self, identifier: str | None, fallback: str) -> str:
        if identifier and identifier.strip():
            return identifier.strip()
        return fallback

    def _extract_named_section(self, body: str, names: set[str]) -> str | None:
        lines = body.splitlines()
        for index, line in enumerate(lines):
            heading = self._normalize_heading(line)
            if heading not in names:
                continue

            block: list[str] = []
            for candidate in lines[index + 1 :]:
                stripped = candidate.strip()
                if block and self._is_heading(candidate):
                    break
                if not block and not stripped:
                    continue
                block.append(candidate)
            text = self.normalize_text("\n".join(block))
            if text:
                return text
        return None

    def _first_paragraph(self, body: str) -> str | None:
        if not body:
            return None
        for paragraph in body.split("\n\n"):
            cleaned = self.normalize_text(paragraph)
            if not cleaned:
                continue
            if cleaned.startswith("#"):
                continue
            if self._extract_list_items(cleaned):
                continue
            return cleaned
        return None

    def _extract_list_items(self, text: str, *, require_checkbox: bool = False) -> list[str]:
        items: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)(?:\[(?P<checked>[ xX])\]\s*)?(?P<item>.+)$", line)
            if not match:
                continue
            if require_checkbox and match.group("checked") is None:
                continue
            item = re.sub(r"\s+", " ", match.group("item").strip())
            if item and item not in items:
                items.append(item)
        return items

    def _normalize_heading(self, line: str) -> str:
        stripped = re.sub(r"^[#>*\s-]+", "", line).strip()
        stripped = stripped.removesuffix(":")
        return stripped.lower()

    def _is_heading(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("#"):
            return True
        normalized = self._normalize_heading(stripped)
        return normalized in {
            "objective",
            "goal",
            "summary",
            "task",
            "acceptance criteria",
            "done when",
            "requirements",
            "checklist",
            "notes",
            "context",
        }
