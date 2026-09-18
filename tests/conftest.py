import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_files():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
    return [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if name.endswith(".ppcl")
    ]
