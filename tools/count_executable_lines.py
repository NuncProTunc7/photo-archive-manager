"""The ~300-line file budget's instrument (design spec section 0, rev 10; M8 review C6).

The budget is NORMATIVE and until now had no counter in the repository. Four milestones
each rebuilt one in a review session and calibrated it against the previous milestone's
published numbers, which is how an instrument drifts: the numbers in the notes were the
only record of what "executable line" meant, and they were produced by four different
throwaway scripts.

THE DEFINITION, which `tests/test_line_budget.py` pins against the v0.1-m7 tag: a
physical line counts if it carries code. Blank lines do not count. Comment lines do not
count. Docstrings do not count - the module's, a class's, a function's - across every
physical line they span, which is the whole point of rev 10 (a `pam/execute/` module is
half prose and the prose is why it fits in one head). A multi-line string that is NOT a
docstring DOES count: it is a value the code carries.

WHY `tokenize` AND `ast` RATHER THAN LINE PREFIXES. CLAUDE.md's rule from the M6 close -
a hand-rolled parser cannot notice that the document is not the language. A counter that
tests `line.startswith("#")` mis-reads a `#` inside a string, and one that tracks `\"\"\"`
by hand mis-reads a docstring containing a quoted docstring. Both are stdlib, both are
real parsers, and neither adds a dependency.
"""

import ast
import io
import sys
import tokenize
from pathlib import Path

# Design spec section 0 rev 10. "~300" is a budget rather than a hard limit, so this
# script reports rather than forbids; the decision about a file over it is a milestone
# decision (planner.py split at M6; write_api.py is M9's, finding F11).
BUDGET = 300

# Structural tokens that carry no code of their own. NEWLINE/NL end a line rather than
# filling one, INDENT/DEDENT are position, and ENCODING/ENDMARKER are the stream's ends.
_STRUCTURAL = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)

_HAS_DOCSTRING = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstring_lines(source: str) -> set[int]:
    """Every physical line occupied by a docstring, from `ast` rather than by eye.

    A docstring is the first statement of a module, class or function AND a bare string
    expression - which is exactly what `ast` reports, so nothing here has to guess. A
    string in any other position is a value and is counted as code.
    """
    lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, _HAS_DOCSTRING) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def count_executable_lines(source: str) -> int:
    """How many physical lines of `source` carry code."""
    carrying: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in _STRUCTURAL:
            continue
        carrying.update(range(token.start[0], token.end[0] + 1))
    return len(carrying - docstring_lines(source))


def count_file(path: Path) -> int:
    return count_executable_lines(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or sorted(
        Path(__file__).resolve().parent.parent.joinpath("pam").rglob("*.py")
    )
    rows = sorted(((count_file(path), path) for path in paths), reverse=True)
    for count, path in rows:
        marker = f" ! over the ~{BUDGET} budget" if count > BUDGET else ""
        print(f"{count:5,}  {path.as_posix()}{marker}")
    over = [path for count, path in rows if count > BUDGET]
    print(f"\n{len(rows):,} files, largest {rows[0][0]:,}, {len(over):,} over {BUDGET}.")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
