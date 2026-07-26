"""Smoke test: the package and its task skeleton import cleanly."""


def test_import_package():
    import ctx_distillery  # noqa: F401


def test_import_distill_session():
    from ctx_distillery.task import DistillSession  # noqa: F401
