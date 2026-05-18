# dynamic_analyst/__init__.py
try:
    from .agent import root_agent
except ImportError as e:
    # Allow import without google.adk for testing
    import warnings
    warnings.warn(f"Could not import agent (google.adk not available): {e}")
    root_agent = None