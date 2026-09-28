I am very optimistic about this direction as a whole. Its true value isn't "putting `.svp/.ust/.vsqx` into SQLite," but rather establishing the relationships between **songs, tuning projects, sources, versions, and actual files**, so that users won't have to rely on folder memory to find things in the future.

However, in the current version of the proposal, there are a few architectural issues that are best resolved before you actually write code. Otherwise, V1 will look very easy to build, but once you have hundreds of songs, with 5 to 20 versions each, the data model will start to constrain features in reverse.

## First, the Conclusion

The current product concept can roughly be broken down into:

> **VocaVault = Music Work Database + Vocal Synth Project Asset Manager + Local Search Engine + Optional File Manager**

This positioning holds up.

What I most recommend you change isn't the UI, but the **Data Model**. The core principle is:

> **Song ≠ Project ≠ File ≠ Version**

Your current design actually mixes these four things together.

---

# 1. The Biggest Issue: Song / Project / File / Version Must Be Separated

For example, you might have:

```text
ゴーストルール
│
├── Original Song
│
├── Project: Ghost Rule - Miku V4X
│   ├── song_v1.vsqx
│   ├── song_v2_tuned.vsqx
│   └── song_final.vsqx
│
├── Project: Ghost Rule - Teto SV
│   ├── ghost_rule.svp
│   └── ghost_rule_final.svp
│
└── Project: Ghost Rule - UTAU Cover
    └── ghost_rule.ust
```

What this actually is:

```text
Song
  ↓
Project
  ↓
Asset / File
  ↓
Revision
```

Instead of:

```text
Song
  ↓
One File
```

### Core Entities I Recommend

```text
Song
 ├── SongName
 ├── SongAlias
 ├── SongCredit
 ├── SongLink
 └── SongMetadata

Project
 ├── belongs_to Song
 ├── ProjectCredit
 ├── IntendedVoice
 ├── ProjectMetadata
 └── Tags

Asset
 ├── belongs_to Project
 ├── physical_path
 ├── management_mode
 ├── format
 ├── checksum
 ├── file_size
 ├── modified_time
 └── parser_metadata

Revision
 ├── belongs_to Asset / Project
 ├── version_label
 ├── parent_revision
 ├── created_time
 └── checksum
```

In practice, `Asset` and `Revision` can also be merged initially into:

```text
project_files
```

And then build a version tree via `parent_file_id`.

That way, the:

> Project Folder

in your UI is just a **logical grouping**, and you won't have to force it to correspond to real folders.

---

# 2. Do Not Make Alias an Array

Right now you have:

> Alternate Names / Aliases: Array of strings

Conceptually that's fine, but in SQLite it's best not to literally do:

```sql
aliases = '["Ghost Rule", "ゴーストルール", "Goosuto Ruuru"]'
```

This will make future searching, deduplication, and language filtering cumbersome.

Recommendation:

```text
songs
song_names
----------
id
song_id
name
language
name_type
source
```

For example:

```text
Ghost Rule
├── ゴーストルール   | Japanese
├── Ghost Rule       | English
├── Goosuto Rūru     | Romaji
└── ゴーストルール    | Primary
```

This way, you can even implement features like:

```text
Search Language:
[All]
[Japanese]
[English]
[Romaji]
```

Furthermore, VocaDB itself categorizes names into default names, other names, and language preferences; the current public API explicitly supports Non-English, Romaji, English, and Unspecified name types, rather than an arbitrary number of "all language translations." Therefore, your data model should allow for more languages, but do not assume VocaDB will necessarily give you translations in every language. ([VocaDB Wiki][1])

---

# 3. VocaDB Integration: Great Idea, But Don't Let It Be the "Authoritative Data Source"

I completely agree with this part.

However, the UI is best designed as:

```text
VocaDB Match Found

ゴーストルール
DECO*27

Confidence: High

[Use VocaDB Metadata]
[Review Changes]
[Cancel]
```

Instead of:

```text
Fetch VocaDB
↓
Overwrite local metadata
```

Because the user's own data should take the highest priority.

I recommend recording the source for each field:

```text
title:
    source = user

english_alias:
    source = vocadb

bpm:
    source = parser

original_author:
    source = vocadb

tuner:
    source = user
```

Only by doing this will you later be able to achieve:

> **Refresh from VocaDB without overwriting user edits**

This will be extremely important.

Additionally, VocaDB officially recommends that clients cache API responses to avoid duplicate requests; a large volume of requests may also trigger rate limits. Therefore, it is best to design a local cache rather than re-querying every time a song is opened. ([VocaDB Wiki][1])

---

# 4. The Search Section Needs a Redesign

This is what I consider to be **most worthy of becoming a core technical feature** of your entire product.

You currently write:

> Fuse.js or SQLite FTS

I would change it to:

```text
SQLite FTS5
      ↓
Candidate Results
      ↓
RapidFuzz / edit-distance reranking
      ↓
Final Results
```

The reason is that SQLite FTS5 is exceptionally well-suited for local Unicode searches, and its official tokenizers include `unicode61` and `trigram`; trigram allows for substring matching. ([SQLite][2])

However:

```text
FTS5 ≠ typo tolerance
```

For example, if a user inputs:

```text
gosht rul
```

FTS5 will not inherently understand that this is:

```text
Ghost Rule
```

Therefore, you can do:

### Layer 1: Normalization

```text
Ghost Rule
ghost-rule
GHOST RULE
```

Unified into:

```text
ghost rule
```

Followed by:

```text
NFKC
casefold
punctuation normalization
whitespace normalization
```

### Layer 2: FTS

Search for:

```text
ghost
```

to quickly retrieve candidates.

### Layer 3: Fuzzy Ranking

Apply the following to the candidates:

```text
Levenshtein
Jaro-Winkler
token similarity
```

to sort them.

In the Python ecosystem, I would directly use `rapidfuzz`—there is no need to introduce JS's Fuse.js just for this part.

---

# 5. More Importantly: Cross-Language Search and Fuzzy Search Are Two Different Things

Take this example of yours:

> Search for `Ghost`
> Find `ゴーストルール` and `ゴーゴー幽霊船`

What is actually at play here is not fuzzy search.

Rather, it is:

```text
Ghost
 ↓
English alias
 ↓
Song A / Song B
```

In other words:

```text
Cross-language retrieval
```

Instead of:

```text
fuzzy matching
```

I suggest directly incorporating the following into your search index:

```text
primary_name
aliases
romanized_names
author_names
project_names
voicebank_names
tags
filename
```

And even:

```text
directory path
```

So when a user searches for:

```text
miku
```

they can simultaneously find:

```text
Hatsune Miku
初音ミク
Miku V4X
Miku NT
miku_final.svp
```

This truly embodies the value of VocaVault.

---

# 6. `Has Tuning?` Should Not Just Be a Boolean

Right now you have:

```text
Has Tuning?
Yes / No / Partial
```

This is a good direction, but I would take it a step further into:

```text
Tuning Status

Unknown
None
Detected
Partial
Substantial
```

Because:

```text
pitch bend exists
```

does not necessarily mean:

```text
manual tuning was performed
```

For instance, some projects might contain default pitch data, auto-generated data, or very minor parameter variations.

Therefore, it's best for the parser to return something like:

```python
TuningInfo(
    pitch_deviation_points=128,
    vibrato_notes=34,
    dynamics_points=421,
    estimated_tuned=True,
)
```

And have the UI convert it into:

```text
Tuning: Detected
Pitch: ✓
Vibrato: ✓
Dynamics: ✓
```

This will be much more powerful than a simple Boolean.

---

# 7. `Has Lyrics` Should Also Be Changed

Currently:

```text
Has Lyrics = Yes / No
```

In practice, you could implement:

```text
Lyrics:
    None
    Partial
    Complete
    Unknown
```

Or even:

```text
Lyrics Coverage: 96%
```

Because:

```text
あ
a
ら
```

are not necessarily all "no lyrics."

For example, a UTAU project might have:

```text
1  あ
2  い
3  う
```

This might already constitute real lyrics.

Meanwhile, another project:

```text
a
a
a
a
```

might just be placeholders.

Thus, the parser shouldn't try to judge using a simple string.

---

# 8. BPM Should Not Be Just a Single Field

This is extremely important.

Right now you have:

```text
BPM = 140
```

However, a modern vocal synth project can entirely have:

```text
0s     128 BPM
40s    140 BPM
90s    160 BPM
```

For instance, OpenUtau's USTX explicitly supports a tempo list rather than just a single BPM. Its format also includes tracks, voice parts, pitch, vibrato, and expression curves. ([GitHub][3])

Therefore, the database is best structured as:

```text
tempo_initial
tempo_min
tempo_max
has_tempo_changes
```

Or even:

```text
tempo_events
```

The actual tempo map can reside in the parser cache, while the UI simply displays:

```text
140 BPM
↳ 3 tempo changes
```

---

# 9. Voicebank Should Be an Attribute of "Each Track"

Right now:

> Intended Voicebank

is placed at the Project layer.

However, a project can completely consist of:

```text
Track 1 → Hatsune Miku
Track 2 → Kasane Teto
Track 3 → Megurine Luka
```

Therefore, it should be:

```text
Project
 └── Tracks
      ├── Track 1
      │    └── Voicebank
      ├── Track 2
      │    └── Voicebank
      └── Track 3
           └── Voicebank
```

The database doesn't necessarily have to store all the complete track data, but it should at least allow:

```text
project_tracks
----------------
id
project_id
track_index
track_name
voicebank
language
```

So that future searches for:

```text
Teto
```

can find all projects utilizing Teto.

---

# 10. Parser Architecture Should Be Built as a Plugin/Adapter

You currently write:

> Custom Python scripts

I recommend designing it from day one as:

```python
class ProjectParser(ABC):

    @property
    def extensions(self) -> list[str]:
        ...

    def detect(self, path) -> bool:
        ...

    def parse(self, path) -> ParsedProject:
        ...
```

Followed by:

```text
parsers/
├── base.py
├── svp.py
├── vsqx.py
├── vpr.py
├── ust.py
├── ustx.py
├── ccs.py
└── ppsf.py
```

Uniformly outputting:

```python
ParsedProject(
    format="SVP",
    format_version="...",
    title="Ghost Rule",
    tempo_events=[...],
    tracks=[...],
    voicebanks=[...],
    lyrics_stats=...,
    tuning_stats=...,
)
```

This way, adding formats later like:

```text
UFData
MusicXML
MIDI
TSS
DV
```

will not pollute the core program.

Currently, the community tool UtaFormatix already covers many formats such as VSQX, VPR, UST, USTX, CCS, SVP, and PPSF. This nicely illustrates why your parser layer is best designed as format adapters, rather than hardcoding every format into the database logic. ([GitHub][4])

Additionally, there is a small detail here that needs correction:

> `.vpr` should not be simply described as "a type of JSON file."

VPR itself is a ZIP container containing project JSON, such as `Project/sequence.json`, and may also contain audio. ([GitHub][5])

Meanwhile, USTX is currently a YAML-based format capable of storing multiple tracks, tempos, pitches, vibratos, and expressions. ([GitHub][3])

Thus, the importer should first distinguish between:

```text
Plain text
XML
JSON
YAML
ZIP container
```

---

# 11. ZIP Import Is a Good Feature, But Exercise Extreme Caution Here

Your workflow:

```text
drag ZIP
 ↓
unpack
 ↓
find UST
 ↓
import
```

The direction is correct.

However, you must guard against ZIP path traversal, such as:

```text
../../../../something
```

as well as:

```text
duplicate filenames
symlinks
huge decompression bombs
unexpected executables
```

Moreover, projects like VPR are themselves containers with internal resources, so:

```text
ZIP import
```

is best handled with a temporary staging area:

```text
Incoming/
    ↓
Scan
    ↓
Parse
    ↓
User confirmation
    ↓
Vault
```

Avoid:

```text
ZIP → Moving directly into the official Vault
```

---

# 12. Managed Mode Requires Special Attention: "What Happens When Metadata Changes?"

This is one of the most common issues your Vault mode will encounter later on.

For example:

```text
Author = DECO*27
Language = Japanese
Title = Ghost Rule
```

resulting in:

```text
SynthV/
└── DECO*27/
    └── Japanese/
        └── Ghost Rule/
```

Half a year later, the user changes the author to:

```text
DECO*27 feat. ...
```

What then?

Should files be moved automatically?

I recommend providing settings:

```text
When metadata changes:

○ Update metadata only
○ Suggest folder relocation
○ Automatically relocate managed files
```

And:

> The actual physical location of the Vault should not serve as the database identity.

The database should contain:

```text
asset_id = UUID
```

with the path merely being:

```text
current_path
```

Otherwise, you will suffer greatly once files are moved.

---

# 13. It Is Recommended to Add Hashes for File Identity

Right now:

```text
File Path
```

is not enough.

For instance:

```text
song_final.svp
```

Today:

```text
C:\Downloads\song_final.svp
```

Tomorrow:

```text
D:\Projects\song_final.svp
```

VocaVault should know:

> This is the same file.

Therefore, at least use:

```text
size
mtime
hash
```

Recommended:

```text
BLAKE3 / SHA-256
```

For large files, you can first use:

```text
size + modified time
```

as a quick check.

Compute the full hash only when necessary.

This enables very useful features like:

```text
Duplicate Project Detected

song_final.svp
↑
Same content as:
Ghost Rule / SynthV / v3

[Keep Both]
[Merge]
[Ignore]
```

This would be a killer feature.

---

# 14. The Position of Management Mode Can Also Be Adjusted

Right now you treat:

```text
Management Mode
```

as Project Metadata.

I lean more towards:

```text
Workspace / Collection
        ↓
Management Policy
        ↓
Asset
```

Because users might have:

```text
Vault A = Managed
Drive D = Indexed
External SSD = Indexed
```

Different assets within the same song can completely come from different workspaces.

For example:

```text
Ghost Rule
├── main.svp
│   └── Managed
│
├── old.ust
│   └── Indexed
│
└── reference.zip
    └── Indexed
```

This feels much more natural than:

```text
Project = Managed
```

---

# 15. Drag & Drop: This Feature Can Be Built and Is Worth Keeping

Conversely, I think your design for this point is quite good.

Qt's drag-and-drop system allows local files to be provided to other applications as `text/uri-list` via `QMimeData.setUrls()`, meaning VocaVault can truly initiate OS-level file dragging rather than simulating "copying filenames." ([Qt Documentation][6])

Roughly like this:

```python
mime = QMimeData()
mime.setUrls([
    QUrl.fromLocalFile(file_path)
])

drag = QDrag(widget)
drag.setMimeData(mime)
drag.exec()
```

In this way:

```text
VocaVault
    ↓ drag
Synthesizer V
    ↓ drop
song.svp
```

is theoretically completely sound.

However, do not promise in the UI copy:

> "Dragging to any Vocal Synth will open it automatically."

Because whether a drop is accepted is ultimately **determined by the target application**.

More accurately:

```text
"Drag project files into supported applications."
```

And retain:

```text
Open With…
```

along with:

```text
Open in Synthesizer V
Open in Vocaloid
Open in OpenUtau
Show in Finder / Explorer
```

as fallbacks.

---

# 16. I Suggest Minor Modifications to the UI

Your:

```text
Left
Center
Right
```

structure is fine.

I would change the center area from a plain data grid to:

```text
┌───────────────────────────────────────────────┐
│ Search...                           + Import │
├─────────────┬─────────────────────────────────┤
│ Filters     │ Ghost Rule                     │
│             │ SynthV • Teto SV                │
│ Engine      │ 3 files • 4 versions           │
│ Language    │ Updated 2 days ago              │
│ Tuning      │                                 │
│ Voicebank   │ ┌─────────────┐                │
│ Tags        │ │ v3 FINAL    │                │
│             │ └─────────────┘                │
└─────────────┴─────────────────────────────────┘
```

In other words:

> **The first layer displays Projects, not Files.**

And then expands:

```text
Ghost Rule
  ├── v3 FINAL.svp
  ├── v2 tuned.svp
  └── v1.svp
```

This naturally integrates your "Version Control & Grouping" into the UI.

---

# 17. I Would Make Status a True Workflow

Do not let it simply be:

```text
Status = Archived
```

You can build:

```text
Idea
↓
Imported
↓
Tuning
↓
Tuned
↓
Mixing
↓
Ready
↓
Released
↓
Archived
```

But allow users to customize it.

Therefore, in the database:

```text
workflow_statuses
```

instead of hardcoding an enum.

---

# 18. Recommended Addition of a Feature You Haven't Written Yet: Health Check

I think this would be particularly fitting for VocaVault.

For example:

```text
⚠ 7 Projects need attention

Ghost Rule
   ⚠ Missing referenced audio

Tell Your World
   ⚠ File moved

Unknown Project
   ⚠ Parser version unsupported

World is Mine
   ⚠ Duplicate detected
```

Each Asset can have:

```text
✓ File exists
✓ Parser succeeded
✓ References valid
⚠ External audio missing
⚠ Voicebank unavailable
⚠ Format version unknown
```

This is much more advanced than an ordinary "file manager."

---

# 19. Recommended Initial Database Schema

I would start in this direction:

```text
songs
-----
id
canonical_title
original_language
created_at
updated_at

song_names
----------
id
song_id
name
language
kind
source

people
------
id
name

song_credits
------------
song_id
person_id
role

projects
--------
id
song_id
name
description
created_at
updated_at

project_tracks
--------------
id
project_id
track_index
name
voicebank
language

project_files
-------------
id
project_id
path
managed
format
format_version
size
mtime
hash
status
created_at

file_metadata
-------------
file_id
initial_bpm
has_tempo_changes
tuning_status
lyrics_status
track_count
parser_version

tags
----
id
name

project_tags
------------
project_id
tag_id

links
-----
id
entity_type
entity_id
url
kind

sources
-------
id
entity_type
entity_id
source_type
source_url
retrieved_at
```

Plus:

```text
fts_search
```

as an SQLite FTS5 virtual table.

---

# 20. Another Very Important Product Principle: The Parser Should Not Modify User Files

The entire program is best governed by:

```text
Parser
    ↓
Read
    ↓
Normalize
    ↓
Database
```

Instead of:

```text
Parser
    ↓
Read
    ↓
Rewrite project
```

VocaVault is an:

> **Asset Manager**

Rather than a:

> **Project Editor**

This drastically reduces the risk of corrupting user projects.

---

# 21. I Actually Recommend Scoping Down V1

The features you have listed are already quite rich.

I would not build the following in the first version:

```text
VSQX
VPR
SVP
UST
USTX
CCS
PPSF
VocaDB
Fuzzy Search
Managed Mode
Indexed Mode
Versioning
OS Drag & Drop
Multi-language
Smart Folders
```

Doing so easily turns the product into one where "there are many features, but every single one is at 70%."

### V0.1

Only do:

```text
SQLite
Song
Project
File
Tag

SVP
UST
VSQX

Import
Parse
Search
Open
Show in Explorer/Finder
```

### V0.2

Add:

```text
VocaDB
Aliases
FTS5
Fuzzy ranking
Version grouping
```

### V0.3

Add:

```text
Managed Vault
Folder templates
Duplicate detection
Health check
```

### V1.0

Then add:

```text
USTX
VPR
CCS
PPSF
advanced drag/drop
smart folders
cross-platform polish
```

This will be much steadier.

---

# What I Consider to Be the True "Killer Feature" of This Project

It is not:

> "Being able to manage `.svp` files"

Because a file manager can already do that.

What has real value is:

```text
                     VocaDB
                       │
                       ▼
Song ─────────── Aliases / Credits
 │
 ├── Project A ─── SVP
 │       ├── v1
 │       ├── v2
 │       └── final
 │
 ├── Project B ─── VSQX
 │
 └── Project C ─── UST
          │
          ▼
       Local Files
          │
          ▼
    Search / Tag / Health
          │
          ▼
    Drag → Vocal Synth
```

This is what constitutes a complete **Vocal Production Asset Library**.

Especially:

> **"I only remember what this song is called in English / who the original author is / that it used Teto / that it's an SVP / that I probably downloaded it about two years ago, but I have no idea where the file is."**

And then VocaVault finds the correct project within a second or two.

This is where the product validates itself.

One more thing I really like: your technology stack, **PySide6 + SQLite**, is extremely well-suited for the first version—it requires neither a server nor Electron. SQLite FTS5 is entirely sufficient for this kind of large-scale local text indexing; Qt also natively provides local file URI drag-and-drop capabilities. ([SQLite][2])

**Therefore, my core advice boils down to just one sentence: first upgrade your database from a "file database" to a relational model of "Work → Project → Asset → Version," and only then start writing the UI.** Once this foundation is set, everything that follows—VocaDB, parsers, search, and Vault—will go much more smoothly.