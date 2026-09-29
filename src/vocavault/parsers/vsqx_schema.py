# Copyright 2020 sdercolin
# SPDX-License-Identifier: Apache-2.0
# Adapted from UtaFormatix3 core/io/Vsqx.kt, commit
# f3c83354f57894492410bbc5ba03f7e97169c92c. Modified for Python metadata
# extraction: canonical element names only; no conversion or timeline rewriting.
# See THIRD_PARTY_NOTICES.md and licenses/Apache-2.0.txt.

VSQ3_TAGS = {
    "posTick": "t",
    "bpm": "v",
    "trackName": "name",
    "musicalPart": "vsPart",
    "durTick": "dur",
    "noteNum": "n",
    "lyric": "y",
    "vsTrackNo": "tNo",
    "phnms": "p",
    "mCtrl": "cc",
    "attr": "v",
    # Voice/style names supplement the upstream musical-event mapping.
    "vBS": "bs",
    "vPC": "pc",
    "vVoiceName": "name",
    "vVoiceID": "id",
    "compID": "id",
    "partStyle": "pStyle",
    "noteStyle": "nStyle",
}

CONTROLLERS = {
    "P": "pitch",
    "PIT": "pitch",
    "S": "pitch",
    "PBS": "pitch",
    "D": "dynamics",
    "DYN": "dynamics",
    "B": "dynamics",
    "BRE": "dynamics",
    "R": "dynamics",
    "BRI": "dynamics",
    "C": "dynamics",
    "CLE": "dynamics",
    "G": "dynamics",
    "GEN": "dynamics",
    "O": "dynamics",
    "OPE": "dynamics",
}
