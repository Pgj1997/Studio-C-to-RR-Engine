Converts showtape signals from Chuck E. Cheese "Studio C" showtapes into `.sshw` files for games that use [RR-Engine](https://github.com/sam-tovar/RR-Engine)

Usage:
From terminal:
`python3 cec_to_sshw.py (data).CEC (video).mp4 -o (output).sshw`

The script expects an MP4 file for the audio.

If the signals are desynced, append `--e-time` to the command followed and mess around with the values and reconvert. The default value is **-1.237**, and from my testing, either **0.1237** or **1.237** are good numbers to test first.
