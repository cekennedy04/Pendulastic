from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Category(str, Enum):
    SAFE_TO_DELETE = "Safe to Delete"
    GITIGNORE_CANDIDATE = "Gitignore Candidate"
    NEEDS_REVIEW = "Needs Review"
    DOC_DRIFT_FIX = "Doc Drift Fix"


@dataclass
class Finding:
    category: Category
    description: str
    command: str
    source: str
    confidence: Optional[int] = None
