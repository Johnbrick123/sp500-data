"""
qbt — a QuantConnect-style backtester for the sp500-data dataset.

    from qbt import Strategy
    class MyIdea(Strategy): ...
    MyIdea().run().report()

See docs/BACKTEST_API.md for the full API and strategies/ for examples.
"""
from .engine import Strategy, Context, Result, market
from .data import load_market
from . import indicators

__all__ = ["Strategy", "Context", "Result", "market", "load_market", "indicators", "run_file"]


def run_file(path, verbose=True, report=True, **overrides):
    """Load a strategy file, run the first Strategy subclass in it, write the report."""
    import importlib.util, inspect, pathlib
    p = pathlib.Path(path)
    spec = importlib.util.spec_from_file_location(p.stem, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    classes = [c for _, c in inspect.getmembers(mod, inspect.isclass) if issubclass(c, Strategy) and c is not Strategy and c.__module__ == mod.__name__]
    if not classes:
        raise SystemExit(f"{path}: no Strategy subclass found")
    cls = classes[-1] if len(classes) == 1 else sorted(classes, key=lambda c: inspect.getsourcelines(c)[1])[-1]
    strat = cls(**overrides)
    strat._source = p.read_text()
    res = strat.run(verbose=verbose)
    if report:
        res.report()
    return res
