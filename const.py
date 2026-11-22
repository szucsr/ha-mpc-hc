"""Constants of the MPC-HC integration: defaults, polling settings and WM_COMMAND codes."""

from enum import IntEnum

DOMAIN = "mpc_hc"

# Defaults offered in the config flow
DEFAULT_NAME = "MPC-HC"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 13579

# Entity service registered by the media player platform
SERVICE_SEND_COMMAND = "send_command"
ATTR_COMMAND = "command"

# Polling intervals in seconds. We poll faster while media is playing so the
# position stays accurate, and slower when nothing is happening.
SCAN_PLAYING = 2
SCAN_IDLE = 10

# While playing, positions are rounded down to this many seconds so entities
# built on them change (and get recorded) less often than every poll
POSITION_RESOLUTION = 5

# Minimum delay between forced refreshes after a command. Home Assistant's
# default is 10 s, which would make rapid commands (volume steps) feel laggy.
REFRESH_COOLDOWN = 0.5

class Cmd(IntEnum):
    # Playback control
    # Play/Pause toggle (used by the media player's play_pause service)
    PLAY = 887
    PAUSE = 888
    PLAY_PAUSE = 889
    STOP = 890

    # Volume (the web server has no "set mute", only a toggle)
    MUTE_TOGGLE = 909
    VOLUME_UP = 907
    VOLUME_DOWN = 908

    # Seek jumps
    JUMP_BACK_SMALL = 899
    JUMP_FWD_SMALL = 900
    JUMP_BACK_MEDIUM = 901
    JUMP_FWD_MEDIUM = 902
    JUMP_BACK_LARGE = 903
    JUMP_FWD_LARGE = 904
    JUMP_BACK_KEYFRAME = 897
    JUMP_FWD_KEYFRAME = 898
    JUMP_TO_BEGINNING = 996
 
    # Speed
    DECREASE_RATE = 894
    INCREASE_RATE = 895
    RESET_RATE = 896

    # Playlist navigation
    PLAYLIST_PREV = 919
    PLAYLIST_NEXT = 920

    # Pan & Scan size
    PANSCAN_RESET = 861
    PANSCAN_INC_WIDTH = 864
    PANSCAN_DEC_WIDTH = 865

    # Video frame
    FRAME_NORMAL = 836
    FRAME_STRETCH = 838
    FRAME_INSIDE = 839

    # Audio track cycling
    AUDIO_PREV = 953
    AUDIO_NEXT = 952
     # Audio delay in 10 ms steps
    AUDIO_DELAY_DEC = 906
    AUDIO_DELAY_INC = 905
    
    # Subtitles on/off
    SUBTITLE_TOGGLE = 956
    # Subtitle track cycling
    SUBTITLE_PREV = 955
    SUBTITLE_NEXT = 954
    # Jumping between subtitle lines
    SUBTITLE_PREV_LINE = 32780
    SUBTITLE_NEXT_LINE = 32781
    # Subtitle delay (the step size is configured in MPC-HC)
    SUBTITLE_DELAY_DEC = 24000
    SUBTITLE_DELAY_INC = 24001

    # Toggle fullscreen, Exit program
    FULLSCREEN_NO_RES_CHANGE = 831
    EXIT = 816

    # Always on top
    ONTOP_NEVER = 883
    ONTOP_ALWAYS = 884
    ONTOP_WHILE_PLAYING = 885
    ONTOP_WHILE_PLAYING_VIDEO = 886
