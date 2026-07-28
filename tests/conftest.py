import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("TRACE_ENABLED", "false")

from src.config import get_settings  # noqa: E402

requires_deepseek = pytest.mark.skipif(
    not get_settings().deepseek_api_key,
    reason="DEEPSEEK_API_KEY is not set; skipping DeepSeek integration tests",
)


@pytest.fixture(scope="session", autouse=True)
def seeded_data():
    """Ensure deterministic demo data exists for tests that need it."""
    from src.config import EVAL_CASES_PATH

    if not EVAL_CASES_PATH.exists():
        import scripts.seed_demo_data as seed

        seed.main()
