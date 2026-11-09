"""Plugin registration contract."""

from unittest.mock import Mock

import pytest

from markitdown_ocr._plugin import register_converters


@pytest.mark.parametrize(
    "kwargs", [{}, {"llm_client": Mock()}, {"llm_model": "vision-model"}]
)
def test_without_ocr_service_builtins_stay_in_charge(kwargs):
    markitdown = Mock()
    register_converters(markitdown, **kwargs)
    markitdown.register_converter.assert_not_called()


def test_with_ocr_service_registers_all_converters():
    markitdown = Mock()
    register_converters(markitdown, llm_client=Mock(), llm_model="vision-model")
    assert markitdown.register_converter.call_count == 4
