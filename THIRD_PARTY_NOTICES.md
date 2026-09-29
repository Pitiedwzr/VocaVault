# Third-party source notices

## UtaFormatix3

Copyright 2020 sdercolin. Licensed under the Apache License, Version 2.0.
The full license is included in `licenses/Apache-2.0.txt`.

Source: https://github.com/sdercolin/utaformatix3
Reference commit: `f3c83354f57894492410bbc5ba03f7e97169c92c`.

`src/vocavault/parsers/vsqx_schema.py` adapts the VSQ3/VSQ4 element mapping
from `core/src/main/kotlin/core/io/Vsqx.kt` into Python. Changes include
canonical element names, voice/style fields, and metadata-only controller
classification. VocaVault does not incorporate UtaFormatix's conversion UI,
pitch conversion pipeline, runtime, or bundled musical assets.

The UST reader was also consulted for the historical `Piches`, `Pitches`, and
`PitchBend` field spellings. No upstream fixtures or project files are distributed.
Upstream attribution and Apache terms continue to apply to adapted source.
