#!/usr/bin/env python3
"""
cec_to_sshw.py  -  Convert a Creative Engineering "Studio C" .CEC showtape into an
RR-Engine / RetroMation .sshw showtape (audio embedded).

Usage:
    python3 cec_to_sshw.py APR0101.CEC APR0101.mp4 [-o APR0101.sshw] [--e-time SECONDS]

    --e-time SECONDS  audio time of the *E marker (default -1.237). Lower = movement
                      plays earlier, higher = later. One beat is 0.4286 s, so
                      -1.237, -0.808, -0.380 and 0.049 are the natural candidates;
                      fine-tune in 0.05 steps from there.

Requires: python3 and ffmpeg on PATH (used only to pull the audio out of the video).

------------------------------------------------------------------------------
CEC FORMAT (as decoded from APR0101.CEC)
------------------------------------------------------------------------------
* Text file, CRLF lines, each line prefixed with a 3 digit line number (stripped).
* The remaining characters form one continuous stream of tokens:
    !          one tick of time with no further changes (a tick ENDS at each '!')
    <d><L>     a bit EDGE inside the current tick:
                 d  digit 0-7   device = d // 2, new state = d % 2 (1 = on, 0 = off)
                 L  letter '@'..'_'   bit number 1..32  (ord(L) - 63)
    *...\\      a show-control command (*M.. / *A.. / *S / *E); not a movement bit
* Every (device, bit) series strictly alternates on/off, starting with on, so
  the stream is edge coded and has to be integrated into per-tick states.
* '*E' (tick 1742 in APR0101) marks the real start of the show; the 1742 ticks
  before it are an idle lead-in.

------------------------------------------------------------------------------
RR-ENGINE .sshw FORMAT (from rshwFormat.cs / UI_ShowtapeManager.cs)
------------------------------------------------------------------------------
A .NET BinaryFormatter (MS-NRBF) stream of:
    class rshwFormat { byte[] audioData; int[] signalData; byte[] videoData; }
    audioData  : a PCM WAV file
    signalData : 60 fps frames. 0 ends a frame, any other value n means bit n-1
                 of the 300-bit frame is on.  Bits 1..150 = top drawer,
                 151..300 = bottom drawer.
    videoData  : unused (engine loads a sibling .mp4 with the same base name)

------------------------------------------------------------------------------
BIT MAPPING USED
------------------------------------------------------------------------------
    CEC device 0 (digits 0/1)  ->  top drawer    1..32   Chuck E. (Studio C Chuck.controller layers 1T..32T)
    CEC device 1 (digits 2/3)  ->  top drawer   33..64   (not read by the Studio C scene; kept, unverified)
    CEC device 2 (digits 4/5)  ->  bottom drawer 1..32   stage lights (LightController, 'B' drawer)
    CEC device 3 (digits 6/7)  ->  bottom drawer 33..64  light bit 33
Edit DEVICE_BASE below if your simulator build assigns these differently.

------------------------------------------------------------------------------
TIMING (measured against the soundtrack, see notes)
------------------------------------------------------------------------------
    TICK_RATE  = 28.898 ticks per second   (audio beat is steady at 140 BPM)
    E_TIME     = -1.237 s audio time at which the *E marker occurs (data starts before the audio;
                          good to within one beat, 0.4286 s, so check by ear)
"""
import argparse, json, os, re, struct, subprocess, sys, tempfile, wave

# ----------------------------------------------------------------- parameters
TICK_RATE = 28.898
E_TIME = -1.237
OUT_FPS = 60
# absolute bit number (1-based, 1..300) = DEVICE_BASE[device] + cec_bit
DEVICE_BASE = {0: 0, 1: 32, 2: 150, 3: 182}


# ------------------------------------------------------------------- CEC parse
def parse_cec(path):
    raw = open(path, "rb").read().decode("latin1")
    body = ""
    for i, line in enumerate(l for l in raw.split("\r\n") if l):
        if line[:3] != "%03d" % i:
            raise ValueError("unexpected line prefix on line %d: %r" % (i, line[:6]))
        body += line[3:]
    frames, cmds, cur = [], [], []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "*":
            j = body.index("\\", i)
            cmds.append((len(frames), body[i:j + 1]))
            i = j + 1
        elif c == "!":
            frames.append(cur)
            cur = []
            i += 1
        elif c == " ":
            i += 1
        elif c.isdigit():
            cur.append((int(c), ord(body[i + 1]) - 63))
            i += 2
        else:
            raise ValueError("unexpected character %r at %d" % (c, i))
    return frames, cmds


def integrate(frames):
    """edge list -> per tick set of bits that are ON, as sorted tuples of absolute bits"""
    state = {}
    out = []
    for f in frames:
        for d, b in f:
            dev, on = d // 2, d % 2
            absbit = DEVICE_BASE[dev] + b
            if on:
                state[absbit] = True
            else:
                state.pop(absbit, None)
        out.append(tuple(sorted(state)))
    return out


# ----------------------------------------------------------------------- audio
def extract_wav(video, out_wav):
    """PCM 16 bit WAV with a canonical 44 byte header (what OpenWavParser expects)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "a.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vn",
                        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", tmp], check=True)
        r = wave.open(tmp)
        params = (r.getnchannels(), r.getsampwidth(), r.getframerate())
        pcm = r.readframes(r.getnframes())
        r.close()
    w = wave.open(out_wav, "wb")
    w.setnchannels(params[0]); w.setsampwidth(params[1]); w.setframerate(params[2])
    w.writeframes(pcm); w.close()
    return params, len(pcm) / (params[0] * params[1] * params[2])


# -------------------------------------------------------------- 60 fps signals
def build_signal(ticks, e_tick, audio_seconds, e_time=E_TIME):
    n_frames = int(audio_seconds * OUT_FPS) + 1
    sig = []
    for f in range(n_frames):
        t_audio = (f + 0.5) / OUT_FPS                 # centre of the 1/60 s frame
        k = int((t_audio - e_time) * TICK_RATE) + e_tick
        bits = ticks[k] if 0 <= k < len(ticks) else ()
        sig.append(0)                                 # frame delimiter
        sig.extend(bits)                              # bit value n == bit index n-1
    return sig, n_frames


# ------------------------------------------------------- NRBF (BinaryFormatter)
ASM = "Assembly-CSharp, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"


def _lps(s):
    b = s.encode("utf-8")
    n = len(b)
    pre = b""
    while True:                                       # 7-bit encoded length
        byte = n & 0x7F
        n >>= 7
        if n:
            pre += bytes([byte | 0x80])
        else:
            pre += bytes([byte]); break
    return pre + b


def write_rshw(path, audio_bytes, signal):
    out = bytearray()
    out += b"\x00" + struct.pack("<iiii", 1, -1, 1, 0)                 # SerializedStreamHeader
    out += b"\x0c" + struct.pack("<i", 2) + _lps(ASM)                  # BinaryLibrary id 2
    members = ["<audioData>k__BackingField", "<signalData>k__BackingField",
               "<videoData>k__BackingField"]
    out += b"\x05" + struct.pack("<i", 1) + _lps("rshwFormat")         # ClassWithMembersAndTypes
    out += struct.pack("<i", len(members))
    for m in members:
        out += _lps(m)
    out += bytes([7, 7, 7])                                            # BinaryType.PrimitiveArray x3
    out += bytes([2, 8, 2])                                            # Byte, Int32, Byte
    out += struct.pack("<i", 2)                                        # library id
    out += b"\x09" + struct.pack("<i", 3)                              # audioData  -> ref 3
    out += b"\x09" + struct.pack("<i", 4)                              # signalData -> ref 4
    out += b"\x0a"                                                     # videoData  -> null
    out += b"\x0f" + struct.pack("<iiB", 3, len(audio_bytes), 2) + audio_bytes
    out += b"\x0f" + struct.pack("<iiB", 4, len(signal), 8) + struct.pack("<%di" % len(signal), *signal)
    out += b"\x0b"                                                     # MessageEnd
    open(path, "wb").write(out)


def read_rshw(path):
    """Minimal NRBF reader for the layout above (used as a self check)."""
    b = open(path, "rb").read()
    p = 0

    def rd(fmt):
        nonlocal p
        v = struct.unpack_from(fmt, b, p); p += struct.calcsize(fmt); return v

    def lps():
        nonlocal p
        n = shift = 0
        while True:
            c = b[p]; p += 1
            n |= (c & 0x7F) << shift; shift += 7
            if not c & 0x80: break
        s = b[p:p + n].decode(); p += n; return s

    assert rd("<B")[0] == 0; rd("<iiii")
    assert rd("<B")[0] == 12; rd("<i"); asm = lps()
    assert rd("<B")[0] == 5; rd("<i"); cname = lps(); (cnt,) = rd("<i")
    names = [lps() for _ in range(cnt)]
    types = list(b[p:p + cnt]); p += cnt
    prim = list(b[p:p + cnt]); p += cnt
    rd("<i")
    refs = []
    for _ in range(cnt):
        t = rd("<B")[0]
        refs.append(rd("<i")[0] if t == 9 else None)
    arrays = {}
    while True:
        t = rd("<B")[0]
        if t == 11: break
        assert t == 15
        oid, ln, pt = rd("<iiB")
        if pt == 2: arrays[oid] = b[p:p + ln]; p += ln
        elif pt == 8: arrays[oid] = list(struct.unpack_from("<%di" % ln, b, p)); p += 4 * ln
    return dict(asm=asm, cls=cname, names=names, types=types, prim=prim,
                audio=arrays.get(refs[0]), signal=arrays.get(refs[1]), video_null=refs[2] is None)


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cec"); ap.add_argument("video")
    ap.add_argument("-o", "--out")
    ap.add_argument("--e-time", type=float, default=E_TIME,
                    help="audio time (s) of the *E marker; lower = data plays earlier. "
                         "Steps of 0.4286 (one beat) are the natural alternatives.")
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.cec)[0] + ".sshw"
    base = os.path.splitext(out)[0]

    frames, cmds = parse_cec(a.cec)
    e_tick = next(t for t, c in cmds if c == "*E\\")
    ticks = integrate(frames)
    print("CEC: %d ticks, %d commands, *E at tick %d" % (len(frames), len(cmds), e_tick))

    with tempfile.TemporaryDirectory() as td:
        wav_path = os.path.join(td, "audio.wav")
        params, secs = extract_wav(a.video, wav_path)
        audio = open(wav_path, "rb").read()
    print("audio: %d ch, %d Hz, %.2f s, %.1f MB" % (params[0], params[2], secs, len(audio) / 1e6))

    sig, n = build_signal(ticks, e_tick, secs, a.e_time)
    write_rshw(out, audio, sig)
    print("wrote %s  (%d frames @ %d fps, %d signal ints)" % (out, n, OUT_FPS, len(sig)))

    # sidecar: show-control commands that have no movement bit, in audio time
    side = [{"audio_time_s": round((t - e_tick) / TICK_RATE + a.e_time, 3), "tick": t, "command": c}
            for t, c in cmds]
    json.dump(side, open(base + "_commands.json", "w"), indent=1)

    r = read_rshw(out)
    assert r["cls"] == "rshwFormat" and r["video_null"] and r["audio"] == audio
    assert r["signal"] == sig
    print("self-check OK: stream re-read and matches")


if __name__ == "__main__":
    main()
