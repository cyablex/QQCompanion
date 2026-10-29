import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/seedvc'))
from streaming_context import install_continuity  # noqa: E402


def test_context_ends_before_overlap_keeps_fixed_voice_and_returns_original_length():
    calls = []
    style = object()

    def render(condition, lengths, prompt, speaker, f0, steps, **kwargs):
        calls.append((condition.copy(), lengths.copy(), prompt.copy(), speaker))
        return condition.transpose(0, 2, 1)

    cfm = SimpleNamespace(inference=render)
    report = install_continuity(cfm, lambda values, dim: np.concatenate(values, axis=dim),
                                context_frames=3, overlap_frames=2)
    prompt = np.array([[[100., 101.]]])
    for start in (0, 8, 16):
        condition = np.array([[[100.], [101.]] + [[float(i)] for i in range(start, start + 10)]])
        output = cfm.inference(condition, np.array([12]), prompt, style, None, 50)
        assert output.shape == (1, 1, 12)
        np.testing.assert_array_equal(output[:, :, 2:], np.arange(start, start + 10)[None, None])
    assert calls[1][0].ravel().tolist() == [100., 101., 5., 6., 7.] + list(range(8, 18))
    assert calls[1][2].ravel().tolist() == [100., 101., 5., 6., 7.]
    assert calls[2][2].ravel().tolist() == [100., 101., 13., 14., 15.]
    assert all(c[3] is style for c in calls)
    assert [c['continuity_frames'] for c in report] == [0, 3, 3]


def test_short_previous_chunk_and_new_song_do_not_import_unrelated_context():
    observed = []

    def render(condition, lengths, prompt, *args, **kwargs):
        observed.append(prompt.shape[-1])
        return condition.transpose(0, 2, 1)

    cat = lambda values, dim: np.concatenate(values, axis=dim)  # noqa: E731
    one = SimpleNamespace(inference=render)
    install_continuity(one, cat, context_frames=86, overlap_frames=16)
    data = np.zeros((1, 5, 1))
    for _ in range(2):
        one.inference(data, np.array([5]), np.zeros((1, 1, 2)), None, None, 50)
    other = SimpleNamespace(inference=render)
    install_continuity(other, cat)
    other.inference(data, np.array([5]), np.zeros((1, 1, 2)), None, None, 50)
    assert observed == [2, 2, 2]
