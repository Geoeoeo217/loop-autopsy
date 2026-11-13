import ast

from loop_autopsy.transformer import (LoopTransformer, classify_calls, compare_spec, method_receivers,
                                      names_in)


def transform(code):
    infos = []

    def register(info):
        info.loop_id = len(infos)
        infos.append(info)
        return info.loop_id

    tree = LoopTransformer(register).visit(ast.parse(code))
    return infos, ast.unparse(tree)


def test_names_in_keeps_order_and_skips_dunders():
    tree = ast.parse("total = total + i\n__secret = 1")
    assert names_in(tree.body) == ["total", "i"]


def test_compare_spec_simple_and_complex():
    assert compare_spec(ast.parse("i < 10", mode="eval").body) == (("name", "i"), "<", ("const", 10))
    assert compare_spec(ast.parse("x != -1", mode="eval").body) == (("name", "x"), "!=", ("const", -1))
    assert compare_spec(ast.parse("a < b < c", mode="eval").body) is None
    assert compare_spec(ast.parse("len(s) > 0", mode="eval").body) is None


def test_classify_calls():
    body = ast.parse("print(x)\ny = len(z)\nlst.append(1)").body
    assert classify_calls(body, {"x", "z", "lst"}) == (True, False)
    assert classify_calls(ast.parse("s = input()").body, {"s"}) == (False, True)
    assert classify_calls(ast.parse("v = helper(v)").body, {"v"})[0] is False
    assert method_receivers(ast.parse("random.random()\nmath.sqrt(2)").body) == ("random",)


def test_while_loop_is_instrumented():
    infos, code = transform("count = 1\nwhile count <= 5:\n    print(count)\n")
    assert len(infos) == 1
    info = infos[0]
    assert info.kind == "while" and info.header == "while count <= 5:"
    assert info.cond_names == ("count",) and info.repeat_safe and not info.waits_for_user
    assert "__loop_autopsy__.begin(0)" in code
    assert "if __loop_autopsy__.due(0):" in code
    assert "__loop_autopsy__.check(0, locals())" in code


def test_for_loop_records_iterable_name():
    infos, _ = transform("for n in nums:\n    nums.append(n)\n")
    assert infos[0].kind == "for" and infos[0].iter_name == "nums"


def test_generator_loops_are_left_alone():
    infos, code = transform("def g():\n    while True:\n        yield 1\n")
    assert infos == [] and "__loop_autopsy__" not in code


def test_nested_loops_keep_outer_repeat_safe():
    infos, _ = transform("while a < 3:\n    b = 0\n    while b < 2:\n        b += 1\n    a += 1\n")
    assert len(infos) == 2
    assert all(i.repeat_safe for i in infos)


def test_paused_transformer_changes_nothing():
    tree = ast.parse("while True:\n    pass\n")
    out = LoopTransformer(lambda info: 0, is_paused=lambda: True).visit(tree)
    assert "__loop_autopsy__" not in ast.unparse(out)
