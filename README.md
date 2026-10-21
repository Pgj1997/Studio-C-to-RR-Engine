Convert showtape singnals from Chuck E. Cheese "Studio C" showtapes, and convert them into `.sshw` files for games that use [RR-Engine](https://github.com/sam-tovar/RR-Engine)

Usage:
From terminal:
`python3 cec_to_sshw.py (data).CEC (video).mp4 [-o (output).sshw]`

The script expects an MP4 file for the audio. If the signals are desynced, mess around with the **E_TIME** value, and re-convert.
