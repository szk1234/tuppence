import pytest

from knowledge.helpers import KnowledgeEnv


@pytest.fixture
def kenv(tmp_path) -> KnowledgeEnv:
    return KnowledgeEnv.create(tmp_path)
