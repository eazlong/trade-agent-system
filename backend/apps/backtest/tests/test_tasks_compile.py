import py_compile
from pathlib import Path


def test_backtest_tasks_module_compiles():
    file_path = Path(__file__).resolve().parents[1] / "tasks.py"
    py_compile.compile(str(file_path), doraise=True)
