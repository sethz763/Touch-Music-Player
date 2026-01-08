from __future__ import annotations

import time

import av


def main() -> int:
    path = r"C:/Users/Seth Zwiebel/Music/All I Need - Radiohead.mp3"
    t0 = time.time()
    c = av.open(path)
    s = next((st for st in c.streams if st.type == "audio"), None)
    if s is None:
        print("no audio stream")
        return 2

    r = av.AudioResampler(format="fltp", layout="stereo", rate=48000)

    samples = 0
    t1 = time.time()
    for packet in c.demux(s):
        for frame in packet.decode():
            outs = r.resample(frame)
            for of in outs:
                arr = of.to_ndarray()
                samples += int(arr.shape[-1])
                if samples >= 48000 * 5:
                    dt_open = t1 - t0
                    dt_dec = time.time() - t1
                    print(f"opened in {dt_open:.3f}s")
                    print(f"decoded {samples} samples in {dt_dec:.3f}s")
                    c.close()
                    return 0

    print(f"EOF after decoding {samples} samples")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
