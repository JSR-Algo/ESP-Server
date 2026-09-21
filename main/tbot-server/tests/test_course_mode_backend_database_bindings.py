"""Exercise the environment expression passed to the actual backend test process."""
import ast
import inspect
from types import SimpleNamespace

import pytest

from scripts import course_mode_release_gate as gate


@pytest.mark.parametrize('required', [
    'RETAINED_TEST_DATABASE_URL',
    'LESSON_RETAINED_TEST_DATABASE_URL',
    'LESSON_STORAGE_TEST_DATABASE_URL',
    'LESSON_LIFECYCLE_HARDENING_TEST_DATABASE_URL',
])
def test_backend_required_databases_use_owned_url(required):
    tree = ast.parse(inspect.getsource(gate._run_backend_native_tests))
    owned_context = next(node for node in ast.walk(tree) if isinstance(node, ast.With)
                         and isinstance(node.items[0].context_expr, ast.Call)
                         and isinstance(node.items[0].context_expr.func, ast.Attribute)
                         and node.items[0].context_expr.func.attr == 'OwnedPostgres')
    call = next(node for node in ast.walk(owned_context) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'run_bounded_command')
    expression = next(keyword.value for keyword in call.keywords if keyword.arg == 'env')
    environment = eval(compile(ast.Expression(expression), '<backend-test-environment>', 'eval'),
                       {'native_environment': {required: 'postgresql://foreign/never-use', 'CI': '1'},
                        'database': SimpleNamespace(url='postgresql://owned/isolated')})
    assert environment[required] == 'postgresql://owned/isolated'
    assert environment['CI'] == '1'
