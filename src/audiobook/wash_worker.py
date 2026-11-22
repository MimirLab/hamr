"""Local 48 kHz reference enhancement workers. This file runs in the optional environment."""

import json
from pathlib import Path
import sys

import numpy as np

RATE = 48000


def fastenhancer(session, waveform):
    fft_size, hop = 1024, 512
    inputs = session.get_inputs()
    if inputs[0].name != 'wav_in' or inputs[0].shape != [1, hop]:
        raise ValueError('Unexpected FastEnhancer waveform graph.')
    state = {item.name: np.zeros(item.shape, np.float32) for item in inputs[1:]}
    padded = np.pad(waveform, (0, fft_size))
    frames = []
    for start in range(0, len(waveform) + fft_size - hop, hop):
        state['wav_in'] = padded[None, start:start + hop]
        frame, *caches = session.run(None, state)
        frames.append(frame[0])
        for item, value in zip(inputs[1:], caches, strict=True):
            state[item.name] = value
    delay = fft_size - hop
    return np.concatenate(frames)[delay:delay + len(waveform)]


def dpdfnet(session, waveform):
    # The exported HR graph expects centered reflect-padded Vorbis windows.
    metadata = session.get_modelmeta().custom_metadata_map
    size, hop = 960, 480
    if metadata.get('profile') != 'dpdfnet2_48khz_hr':
        raise ValueError('Unexpected DPDFNet graph.')
    window = np.sin(np.pi / 2 * np.sin(np.pi * (np.arange(size) + .5) / size) ** 2)
    # Flush through the two-window delay and one overlap window.
    padded = np.pad(np.pad(waveform, (0, 3 * size)), (hop, hop), mode='reflect')
    frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::hop]
    spectrum = np.fft.rfft(frames * window)
    features = np.stack((spectrum.real, spectrum.imag), axis=-1).astype(np.float32)
    state = np.zeros(session.get_inputs()[1].shape, dtype=np.float32)
    spec_count = int(metadata['spec_norm_state_size'])
    erb_count = int(metadata['erb_norm_state_size'])
    state[:erb_count] = np.fromstring(metadata['erb_norm_init'], sep=',', dtype=np.float32)
    state[erb_count:erb_count + spec_count] = np.fromstring(metadata['spec_norm_init'], sep=',', dtype=np.float32)
    output = np.zeros(len(padded))
    weights = np.zeros(len(padded))
    for index, frame in enumerate(features):
        estimated, state = session.run(None, {'spec': frame[None, None], 'state_in': state})
        real, imaginary = estimated[0, 0].T
        start = index * hop
        output[start:start + size] += np.fft.irfft(real + 1j * imaginary) * window
        weights[start:start + size] += window ** 2
    np.divide(output, weights, out=output, where=weights > 1e-10)
    start = hop + 2 * size
    return output[start:start + len(waveform)].astype(np.float32)


def main(engine, model, source, destination):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(model, options, providers=['CPUExecutionProvider'])
    if session.get_providers() != ['CPUExecutionProvider']:
        raise ValueError('Expected the pinned CPU provider.')
    wave = np.load(source, allow_pickle=False)
    if wave.ndim != 1 or not RATE <= len(wave) <= 34 * RATE or not np.isfinite(wave).all():
        raise ValueError('Invalid 48 kHz mono preparation input.')
    adapter = {'fastenhancer': fastenhancer, 'dpdfnet': dpdfnet}[engine]
    result = adapter(session, wave)
    if result.shape != wave.shape or not np.isfinite(result).all():
        raise ValueError('The worker lost samples or returned invalid audio.')
    np.save(destination, result.astype('<f4', copy=False), allow_pickle=False)
    print(json.dumps({'provider': 'CPUExecutionProvider', 'runtime': ort.__version__,
                      'samples': len(result), 'sample_rate': RATE}))


if __name__ == '__main__':
    main(*sys.argv[1:])
