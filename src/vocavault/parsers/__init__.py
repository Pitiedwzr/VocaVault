"""Built-in project parsers."""

from vocavault.parsers.base import ParserLimits, ProjectParser
from vocavault.parsers.svp import SvpParser

__all__ = ["ParserLimits", "ProjectParser", "SvpParser"]
