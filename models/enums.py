"""models/enums.py — shared enumerations used across the app."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED)


class OutputFormat(str, Enum):
    DOCX = "docx"
    PDF = "pdf"
    TXT = "txt"


class TranslationBackend(str, Enum):
    """Which engine performs the bulk source -> target translation.

    ARGOS  — local Argos Translate models. Fully offline, no text ever
             leaves the machine. This is (and remains) Lekha's default.
    GOOGLE — Google Translate via deep-translator. Much faster and uses
             almost no CPU, but every chunk of the document is sent to
             Google's servers. Strictly opt-in for that reason.
    """

    ARGOS = "argos"
    GOOGLE = "google"

    @property
    def is_online(self) -> bool:
        """True if using this backend transmits document text off-device."""
        return self is TranslationBackend.GOOGLE

    @property
    def label(self) -> str:
        return {
            TranslationBackend.ARGOS: "Argos Translate (offline)",
            TranslationBackend.GOOGLE: "Google Translate (online)",
        }[self]
