# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Generate the add-on's built-in sounds (addon/sounds/*.wav).

The sounds are synthesized here rather than taken from elsewhere, so they carry the add-on's license.
Run from the repository root: python tools/makeSounds.py
"""

import math
import os
import struct
import wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addon", "sounds")


def tone(freq, seconds, volume=0.35, attack=0.005, decay=None):
	"""A sine tone with a short attack and an exponential decay."""
	n = int(RATE * seconds)
	decay = decay or seconds / 4
	samples = []
	for i in range(n):
		t = i / RATE
		env = min(1.0, t / attack) * math.exp(-t / decay)
		# A little of the second harmonic makes it less piercing than a pure sine.
		value = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t)
		samples.append(volume * env * value / 1.25)
	return samples


def silence(seconds):
	return [0.0] * int(RATE * seconds)


def mix(*parts):
	out = []
	for p in parts:
		out.extend(p)
	return out


def write(name, samples):
	path = os.path.join(OUT, name + ".wav")
	with wave.open(path, "wb") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(RATE)
		w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
	print(path)


os.makedirs(OUT, exist_ok=True)
# Two rising notes, soft.
write("chime", mix(tone(880, 0.12, decay=0.06), tone(1318.5, 0.25, decay=0.09)))
# One short high blip.
write("blip", tone(1760, 0.07, volume=0.3, decay=0.02))
# Three quick notes, more insistent.
write(
	"alert",
	mix(
		tone(988, 0.09, decay=0.05),
		silence(0.03),
		tone(988, 0.09, decay=0.05),
		silence(0.03),
		tone(1480, 0.2, decay=0.08),
	),
)
