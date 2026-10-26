import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations/seedvc"))
from pitch_policy import reference_register


def test_mixed_register_raises_low_phrase_and_preserves_high_phrase_and_silence():
    source = [110, 123.47, 130.81] * 20 + [0] * 40 + [220, 246.94, 261.62] * 20
    shifted, report = reference_register(source, [240] * 50)
    assert shifted[:60] == [v * 2 for v in source[:60]]
    assert shifted[60:] == source[60:]
    assert [p["semitones"] for p in report["phrases"]] == [12, 0]


def test_changing_reference_changes_register_without_gender_specific_constants():
    shifted, report = reference_register([240] * 60, [120] * 60)
    assert shifted == [120] * 60 and report["phrases"][0]["semitones"] == -12


def test_unvoiced_reference_keeps_source_and_reports_no_adjustment():
    source = [0] * 20 + [110] * 60
    shifted, report = reference_register(source, [0] * 50)
    assert shifted == source and report["reason"] == "reference_unvoiced"
