import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations/seedvc"))
from pitch_policy import reference_register


def test_mixed_register_preserves_every_interval_with_one_global_octave():
    source = [110, 123.47, 130.81] * 20 + [0] * 40 + [220, 246.94, 261.62] * 20
    shifted, report = reference_register(source, [240] * 50)
    assert shifted == source
    assert [p["semitones"] for p in report["phrases"]] == [0, 0]
    assert shifted[100] / shifted[0] == source[100] / source[0]


def test_low_speaking_reference_never_pushes_a_high_sung_note_down():
    shifted, report = reference_register([240] * 60, [120] * 60)
    assert shifted == [240] * 60 and report["phrases"][0]["semitones"] == 0


def test_low_song_can_raise_one_octave_without_folding_its_high_chorus():
    source = [110] * 160 + [0] * 40 + [440] * 30
    shifted, report = reference_register(source, [240] * 50)
    assert shifted[:160] == [220] * 160 and shifted[-30:] == [880] * 30
    assert shifted[160:200] == [0] * 40
    assert report['semitones'] == 12


def test_unvoiced_reference_keeps_source_and_reports_no_adjustment():
    source = [0] * 20 + [110] * 60
    shifted, report = reference_register(source, [0] * 50)
    assert shifted == source and report["reason"] == "reference_unvoiced"
