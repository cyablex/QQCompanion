# ruff: noqa: E402
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from chat_punctuation import without_decorative_waves


def test_decorative_wave_reduced_without_losing_word_boundaries():
    assert without_decorative_waves("在呢～你先忙，我等你~") == "在呢，你先忙，我等你"
    assert without_decorative_waves("Hello～主人") == "Hello，主人"


def test_code_paths_urls_ranges_and_markdown_are_preserved():
    text = "`~mask` 和 ```python\nresult = ~value\n```，~/folder，https://a.test/~user，1~5，1～5，A～Z，三～五，周一～周五，~~删除~~，取反写成~x"
    assert without_decorative_waves(text) == text
