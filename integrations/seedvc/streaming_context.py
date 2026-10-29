"""Keep recently generated singing as acoustic context between Seed-VC chunks.

The fixed target reference and speaker embedding remain present on every call.
Only context preceding the repeated overlap is reused, so source timing and
returned mel lengths stay compatible with the upstream streaming loop.
"""


def install_continuity(cfm, concatenate, context_frames=86, overlap_frames=16):
    original = cfm.inference
    previous_condition = previous_mel = None
    calls = []

    def continuous(condition, lengths, prompt, style, f0, steps, **kwargs):
        nonlocal previous_condition, previous_mel
        reference_frames = prompt.shape[-1]
        current = condition[:, reference_frames:]
        count = min(context_frames, max(0, previous_condition.shape[1] - overlap_frames)) if previous_condition is not None else 0
        augmented = condition
        acoustic = prompt
        if count:
            stop = previous_condition.shape[1] - overlap_frames
            recent_condition = previous_condition[:, stop - count:stop]
            recent_mel = previous_mel[:, :, stop - count:stop]
            augmented = concatenate((condition[:, :reference_frames], recent_condition, current), dim=1)
            acoustic = concatenate((prompt, recent_mel), dim=2)
        result = original(augmented, lengths + count, acoustic, style, f0, steps, **kwargs)
        if count:
            result = concatenate((result[:, :, :reference_frames], result[:, :, reference_frames + count:]), dim=2)
        previous_condition, previous_mel = current, result[:, :, reference_frames:]
        calls.append(dict(reference_frames=reference_frames, continuity_frames=count,
                          source_frames=current.shape[1], returned_frames=result.shape[-1]))
        return result

    cfm.inference = continuous
    return calls
