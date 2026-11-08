"""Preserved Person 1 executor is available separately; no generated execution."""
EXECUTION_ENABLED = False


def execute_generated(*args, **kwargs):
    raise RuntimeError("Generated SQL execution is disabled in this demo")
