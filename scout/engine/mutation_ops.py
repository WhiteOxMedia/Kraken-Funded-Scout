"""THE MUTATION OPERATORS (diag_mutation_sweep's engine, extracted to
its own module so the sweep's verdicts rest on verifiable code).

mutate_source(src) -> (mutated_source, count): conservative mechanical
reverts of a guarded module — every `return True`<->`return False`,
every comparison operator flipped, every non-trivial `if` branch
disabled (the dead-guard check), every non-zero numeric literal +/-10%.
Unparseable input returns (src, 0).

self_test() MUST pass before the sweep reports anything: an inert
mutator would label every drive VACUOUS, and a false vacuity verdict
is worse than no verdict.
"""
from __future__ import annotations

import ast

REPL = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}


def mutate_source(src: str) -> tuple[str, int]:
    """Conservative mechanical reverts; (mutated_source, count).
    Unparseable input returns (src, 0)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src, 0
    counter = {"n": 0}

    class M(ast.NodeTransformer):
        def visit_Return(self, node):
            self.generic_visit(node)
            v = node.value
            if isinstance(v, ast.Constant) and v.value is True:
                counter["n"] += 1
                return ast.copy_location(ast.Return(
                    value=ast.Constant(value=False)), node)
            if isinstance(v, ast.Constant) and v.value is False:
                counter["n"] += 1
                return ast.copy_location(ast.Return(
                    value=ast.Constant(value=True)), node)
            return node

        def visit_Compare(self, node):
            self.generic_visit(node)
            new_ops = []
            changed = False
            for op in node.ops:
                cls = REPL.get(type(op))
                if cls is not None:
                    new_ops.append(cls())   # an INSTANCE — the class
                    changed = True          # itself would crash unparse
                else:
                    new_ops.append(op)
            if changed:
                counter["n"] += 1
                node.ops = new_ops
            return node

        def visit_If(self, node):
            self.generic_visit(node)
            if isinstance(node.test, ast.Constant):
                return node
            counter["n"] += 1
            # THE DEAD-GUARD CHECK: the guarded branch never runs
            return ast.copy_location(ast.If(
                test=ast.Constant(value=False), body=node.body,
                orelse=node.orelse), node)

        def visit_Constant(self, node):
            self.generic_visit(node)
            v = node.value
            if isinstance(v, bool) or v is None:
                return node
            if isinstance(v, float) and v != 0.0:
                counter["n"] += 1
                node.value = v * 1.1
            elif isinstance(v, int) and v != 0 and abs(v) < 10 ** 9:
                bumped = int(v * 1.1)
                if bumped == v:
                    bumped = v + 1
                counter["n"] += 1
                node.value = bumped
            return node

    new_tree = M().visit(tree)
    if counter["n"] == 0:
        return src, 0
    ast.fix_missing_locations(new_tree)
    # NO SWALLOW HERE (the stateclobber lesson, learned on my own
    # tool twice in one night): a transform that cannot unparse is a
    # BUG IN THE MUTATOR, and masking it as count=0 labels healthy
    # drives VACUOUS. Raise loudly instead.
    return ast.unparse(new_tree), counter["n"]


def self_test() -> None:
    """Fail LOUDLY if the mutator is inert — call before any verdict."""
    snippet = ("def f(flag):\n"
               "    if flag:\n"
               "        return True\n"
               "    return False\n"
               "x = 1 if 5 > 3 else 0\n")
    mut, n = mutate_source(snippet)
    assert n >= 3, f"mutation engine INERT (count={n}) — verdicts would lie"
    assert "return False" in mut and "if False:" in mut, \
        "mutation output missing expected reverts"
    # and the result must EXECUTE and BEHAVE DIFFERENTLY somewhere
    # (a mutant identical to the original would vacuously pass)
    ns: dict = {}
    exec(compile(mut, "<mutation-selftest>", "exec"), ns)
    orig: dict = {}
    exec(compile(snippet, "<mutation-selftest-orig>", "exec"), orig)
    assert (ns["f"](True), ns["f"](False)) \
        != (orig["f"](True), orig["f"](False)), \
        "mutant is behaviorally identical to the original"


if __name__ == "__main__":
    self_test()
    print("mutation engine self-test: OK")
