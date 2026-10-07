import random

import numpy as np

from fff.hashing.video_similarity import (
    VideoSignature, _crop_borders, find_similar_pairs, group_pairs, signature_distance,
)


def _flip_bits(value: int, bits: int, rng: random.Random) -> int:
    for pos in rng.sample(range(64), bits):
        value ^= 1 << pos
    return value


def _signature(hashes, **kw):
    return VideoSignature(frame_hashes=list(hashes), **kw)


def test_identical_signatures_have_zero_distance():
    rng = random.Random(1)
    sig = _signature(rng.getrandbits(64) for _ in range(16))
    assert signature_distance(sig, sig) == 0


def test_reencode_with_small_bit_noise_matches():
    rng = random.Random(2)
    base = [rng.getrandbits(64) for _ in range(16)]
    lq = _signature(_flip_bits(h, 3, rng) for h in base)
    assert signature_distance(_signature(base), lq) <= 3


def test_trimmed_excerpt_matches_in_either_direction():
    rng = random.Random(3)
    full = [rng.getrandbits(64) for _ in range(16)]
    # An excerpt covering the first half, sampled at its own 16 points
    excerpt = [full[i // 2] for i in range(16)]
    a, b = _signature(full), _signature(excerpt)
    assert signature_distance(a, b) == 0
    assert signature_distance(b, a) == 0


def test_unrelated_videos_are_far_apart():
    rng = random.Random(4)
    a = _signature(rng.getrandbits(64) for _ in range(16))
    b = _signature(rng.getrandbits(64) for _ in range(16))
    assert signature_distance(a, b) > 16


def test_find_pairs_matches_scalar_distance_and_groups():
    rng = random.Random(5)
    base = [rng.getrandbits(64) for _ in range(16)]
    sigs = [
        _signature(base),
        _signature(_flip_bits(h, 2, rng) for h in base),
        _signature(rng.getrandbits(64) for _ in range(16)),
        _signature(_flip_bits(h, 4, rng) for h in base),
    ]
    pairs = find_similar_pairs(sigs, max_distance=16)
    for i, j, d in pairs:
        assert d == signature_distance(sigs[i], sigs[j])
    assert {(i, j) for i, j, _ in pairs} == {(0, 1), (0, 3), (1, 3)}

    groups = group_pairs(len(sigs), pairs)
    assert len(groups) == 1
    members, worst = groups[0]
    assert sorted(members) == [0, 1, 3]
    assert worst == max(d for _, _, d in pairs)


def test_quality_prefers_resolution_then_bitrate():
    hd = _signature([0], width=1920, height=1080, bitrate=1_000_000)
    sd = _signature([0], width=640, height=480, bitrate=9_000_000)
    hd_hi = _signature([0], width=1920, height=1080, bitrate=8_000_000)
    assert max([sd, hd, hd_hi], key=VideoSignature.quality_key) is hd_hi


def test_crop_borders_removes_letterbox():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[60:420, 80:560] = 200
    cropped = _crop_borders(frame)
    assert cropped.shape[:2] == (360, 480)


def test_crop_borders_keeps_dark_frames():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert _crop_borders(frame).shape == frame.shape
