import pytest
from fake_llm import FakeLLM

from bidbrief.llm import LLMError, extract_json
from bidbrief.schemas import LLMAnswer


def test_invalid_json_is_retried_once(data_dir):
    llm = FakeLLM(invalid_first=True)
    out = llm.generate('QUESTION: When are bids due?\n<excerpt file="a.pdf" page="1" section="">\n'
                       "Sealed bids are due at noon on Friday at the town hall.\n</excerpt>", LLMAnswer)
    assert out.found and llm.calls == 2


def test_invalid_twice_raises(data_dir):
    class Broken(FakeLLM):
        def _call(self, system, prompt, images):
            self.calls += 0
            return "not json at all"

    with pytest.raises(LLMError):
        Broken().generate("QUESTION: x", LLMAnswer)


def test_extract_json_handles_fences():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here you go: {"a": 1} thanks') == '{"a": 1}'
