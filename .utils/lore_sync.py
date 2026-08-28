#!/usr/bin/env python3
"""Sync the lore tables against the Deepwoken wiki mirror.

The tool owns MECHANICAL facts only. It knows exactly three actions:

  Create  a skeleton row (name, aliases, wiki) for a lore page that has no row yet.
  AddRef  one structured id to one relation field of a row, when that id is not there.
  Set     a row's `desc` from its wiki page's descriptive prose, or its `additional_info`
          from the page's Trivia section, cleaned and with wikilinks rewritten to tokens.

There is no fourth action, which is what keeps it out of the written prose. It never
writes `goals`, never touches `books/` or `dialogs/`, and never removes a value. `desc`
and `additional_info` are the two fields it may rewrite, because both are a transcription
of a named wiki section and not a judgment. Wikilink-derived token candidates for the
written fields are report output, because naming an entity inside a sentence needs
judgment that a substring match does not have.

Modes:
  --check (default)  dry run, writes .tmp/lore_sync_report.md
  --apply            performs the Create, AddRef and Set actions, then rewrites the report
  --refresh-desc     plan a desc for every row with a wiki page, not only the rows that
                     have no desc yet
  --refresh-trivia   the same for `additional_info`

`python .utils/bundle.py` and the validator must run after --apply, in that order.
"""

import argparse
import html
import json
import re
from collections import defaultdict, namedtuple
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MIRROR = REPO.parent / "deepwoken-wiki" / "index"
REPORT = REPO / ".tmp" / "lore_sync_report.md"

# The only directories the tool may write. `books` and `dialogs` are not here and never
# will be: their content is transcription, not a mechanical fact about a wiki page.
TABLES = ("entities", "locations", "factions")
NAMESPACE = {"entities": "entity", "locations": "location", "factions": "faction"}

# The complete key set of a created row. Nothing else can enter through Create.
SKELETON_FIELDS = ("name", "aliases", "wiki")

# The only relation fields AddRef may append to. The validator owns which namespace each
# one's targets may land in.
REF_FIELDS = {
    "entities": ("member_of", "found_at", "leads"),
    "locations": ("part_of",),
    "factions": ("part_of",),
}

# Hand-written fields. Every action asserts it left each of them untouched.
PROSE_FIELDS = ("goals",)

# The tool's own fields, and the wiki section each one transcribes. Set writes one of these
# and asserts the other came out untouched.
OWNED_FIELDS = ("desc", "additional_info")

# Canonical key order of a lore row, so an added relation field lands where the
# hand-written rows put it instead of after `wiki`. A field missing from this tuple is
# dropped by the two actions that rewrite a row, so every lore field belongs here.
FIELD_ORDER = (
    "name", "aliases", "official", "desc", "additional_info",
    "member_of", "leads", "found_at", "kin_of", "wields", "holds",
    "part_of", "goals", "wiki",
)

# A key is only reachable if a token can spell it: the validator's grammar is
# `](namespace:key)` with the key limited to lowercase ascii, digits and underscores.
KEY_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")

# Wiki index and mechanic pages that carry a lore category. They describe a class of
# thing or a game system, not a single character, place or faction.
HUB_PAGES = {
    "Attunement Trainers",
    "Bosses",
    "Chime of Dwelling",
    "Deep Shrines",
    "Dungeons",
    "Dwelling Charm",
    "Faction Ambushes",
    "Factions & Groups",
    "Guild Banners",
    "Guild Bases",
    "Guilds",
    "Loot Bag Gacha",
    "Quests",
    "World Events",
}

# Pages about the manga and other retellings. Their characters are not game lore.
NON_CANON = "Non-canon articles"

# Infobox `name` and `alias` cells that say the wiki does not know the name. Letting one
# through would make the placeholder an alias, and every other page that says the same
# word would then resolve to that row.
PLACEHOLDERS = {"unknown", "unnamed", "none", "n/a", "randomized", "varies", "various"}

# The wiki's own tagging, first match wins.
#   `\bNPCs?\b` catches the qualified forms, "Etrean NPCs" and "NPCs located in the First
#   Layer", alongside bare "NPCs".
#   The faction rule reads past "Factions", because the wiki files a group that no longer
#   exists, or that is a church, a council or a house, under its own category instead.
#   `Dungeons` is in the location rule because two dungeon pages carry no Locations tag.
CATEGORY_RULES = (
    ("entities", re.compile(r"\bNPCs?\b|\bBosses\b|^Drowned Gods$")),
    ("factions", re.compile(r"^(Factions|Guilds|Councils|Religious groups)$"
                            r"|^Families and lineages$"
                            r"|\borganizations?\b"
                            r"|^Departments and divisions of ", re.I)),
    ("locations", re.compile(r"\blocations?\b|^Dungeons$", re.I)),
)

# Cut content and content that never shipped carry no NPCs tag even when the page is a
# real lore figure, so CATEGORY_RULES misses them. These marker categories say the page
# is about a person. A cut page matching none of them is left alone, and that silence is
# what keeps cut items, weapons, enchantments and species out of the tables.
CUT_CATEGORIES = {"Cut Content", "Content not present in-game"}
CUT_MARKERS = re.compile(
    r"^Individuals with "               # the pronoun categories, on every person page
    r"|\bcharacters?\b"                 # "Deceased characters", "Characters mentioned in books"
    r"|^Members of "                    # family, lineage and expedition membership
    r"|\bpersonnel\b"                   # "Naval personnel of The Central Authority"
    r"|^(Prophets|Inquisitors|Wardens|Prisoners) of "
    r"|^(Celestials|Hivelords|Convocants|Gunsmiths|Justicars|Songseekers|Lightkeepers)$"
    r"|^(Old Stewards|Scholars and researchers|Maestros of the Vigils)$"
    r"|^(Heads of government and state|Kings of Etrea)$")

# The Circle of Honour is an organization the wiki gives its own category and no Factions
# tag, and that category also sits on mechanic pages, so a rule over it would drag those
# in. One organization and its one member page is not a pattern, so it is a list of titles.
ALLOW_LIST = {"Circle of Honour": "factions", "The Inheritor": "entities"}

CITATION = re.compile(r"\[\\?\[?\d+\\?\]?\](?:\(#cite[^)]*\))?")
VARIANTS = re.compile(r"^Variants: (.+)$", re.M)
# The wiki wraps an href in angle brackets whenever it contains a parenthesis, so the
# bracketed form has to be matched as a whole before the parenthesis closes the link.
LINK = re.compile(r"\[([^\]]+)\]\((<[^>]*>|[^()<>]*)\)")
INFOBOX_ROW = re.compile(r"^\| ([a-z][a-z ]*) \| (.*?) \|$", re.M)
DISAMBIGUATOR = re.compile(r"\s*\([^()]*\)$")
QUALIFIER = re.compile(r"\s*\([^()]*\)")
TERRITORY = re.compile(r"^TERRITORY OF (.+)$", re.M)
TOKEN = re.compile(r"\]\(([a-z_]+:[a-z0-9_]+)\)")

# Where a page keeps its own descriptive block. `Desc\w*` also takes the handful of pages
# that spell the heading "Descrition" or "Descrption". Most location pages carry no
# Description and file the same block under Overview, so that is the second choice.
DESCRIPTION = re.compile(r"^##[ \t]*Desc\w*[ \t]*$", re.M | re.I)
OVERVIEW = re.compile(r"^##[ \t]*Overview[ \t]*$", re.M | re.I)
SECTION = re.compile(r"^## ", re.M)

# Where a page keeps its asides about the subject. A handful of pages head the same list
# "Notes" instead.
TRIVIA = re.compile(r"^##[ \t]*Trivia[ \t]*$", re.M | re.I)
NOTES = re.compile(r"^##[ \t]*Notes[ \t]*$", re.M | re.I)

HTML_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
LINE_BREAK = re.compile(r"<br\s*/?>", re.I)
HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# The banners the wiki puts above an article: the gamemode a page applies to, and the two
# that say how much of the subject exists in game.
PAGE_NOTICE = re.compile(
    r"This page describes content that is exclusive to"
    r"|This content is not accessible outside of this gamemode"
    r"|You are currently viewing the version of this content"
    r"|The subject of this page is mentioned in-game"
    r"|This game content bears more significance")

# Page furniture rather than prose: the wiki's variant and territory banners, the
# `LocationL <name> LocationR` placeholder an image left behind, and the epigraph above
# the article.
FURNITURE = re.compile(
    r"^Variants: "
    r"|^LocationL .+ LocationR$"
    r"|^(LAWLESS )?TERRITORY OF "
    r"|^[“―—]")

HEADING = re.compile(r"^(#+)[ \t]*(.*)$")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
# A paragraph that is one bold run and nothing else, which is what a subsection heading
# becomes.
BOLD_LINE = re.compile(r"\*\*(?:(?!\*\*).)+\*\*")

# Stands in for a table until the lines have been read, so that the sentence introducing a
# table can be found next to it. A NUL byte cannot collide with anything in the mirror.
TABLE_MARK = "\x00"

# A desc this short is a caption or a stub sentence, not the page's descriptive block.
SHORT_DESC = 40


def name_to_identifier(s: str) -> str:
    """Port of `deepwoken::util::name_to_identifier`. Order matters: ': ' collapses to a
    single space before spaces become underscores."""
    s = s.replace(": ", " ")
    s = s.replace(" ", "_")
    for ch in "[]'(),":
        s = s.replace(ch, "")
    s = s.replace("-", "_")
    return s.lower()


# --------------------------------------------------------------------------- the mirror


def parse_page(path: Path, mirror: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None

    title, categories = None, []
    for line in text[4:end].split("\n"):
        if line.startswith("title:"):
            title = line[len("title:"):].strip().strip('"')
        elif line.startswith("categories:"):
            raw = line[len("categories:"):].strip()
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw[1:-1]
            categories = [c.strip().strip('"') for c in raw.split(",") if c.strip()]

    if not title:
        return None
    return {
        "file": path.relative_to(mirror).as_posix(),
        "title": title,
        # A subpage title carries its parent, "Second Layer/New Kyrsa". The subject is the
        # last segment, and that is what the row is named after.
        "name": title.rsplit("/", 1)[-1].strip(),
        "categories": categories,
        "body": text[end + 5:],
    }


def bucket(page):
    """The table a page belongs in, and the rule that put it there. `None` for a page the
    tool has no opinion about."""
    if page["title"] in ALLOW_LIST:
        return ALLOW_LIST[page["title"]], "allow-list"

    categories = page["categories"]
    if NON_CANON in categories:
        return None, None

    for table, rule in CATEGORY_RULES:
        hit = next((c for c in categories if rule.search(c)), None)
        if hit:
            return table, f"category {hit!r}"

    if CUT_CATEGORIES & set(categories):
        hit = next((c for c in categories if CUT_MARKERS.search(c)), None)
        if hit:
            return "entities", f"cut content, marker category {hit!r}"
    return None, None


def infobox(page) -> list[tuple[str, str]]:
    body = page["body"]
    head = body[: body.find("\n\n## ")] if "\n\n## " in body else body
    return INFOBOX_ROW.findall(head)


def cell_names(cell: str) -> list[str]:
    """The plain names in an infobox cell. Citations and parenthetical qualifiers go
    first, because both hide commas, then the cell splits on the commas that are left.
    A cell carrying markup is not a name list and yields nothing."""
    cell = QUALIFIER.sub("", CITATION.sub("", cell)).strip()
    if not cell or any(ch in cell for ch in "[]()<>|*_\"/"):
        return []
    return [part.strip() for part in cell.split(",") if part.strip()]


def infobox_aliases(page) -> list[str]:
    out = []
    for field, cell in infobox(page):
        if field in ("name", "alias"):
            out.extend(name for name in cell_names(cell)
                       if len(name) <= 60 and name.lower() not in PLACEHOLDERS)
    return out


def variant_targets(page, by_file) -> list[str]:
    """Filenames the page's `Variants:` line points at. That line is how the wiki says a
    set of pages is one subject, a character or place written up once per gamemode, so the
    set gets one row."""
    match = VARIANTS.search(page["body"])
    if not match:
        return []
    return [t for t in (resolve_link(page, target) for _, target in LINK.findall(match.group(1)))
            if t in by_file]


def resolve_link(page, target: str) -> str:
    """A wikilink href as a mirror-relative filename."""
    target = unquote(target.strip("<>")).split("#")[0]
    if not target.endswith(".md"):
        return ""
    parent = Path(page["file"]).parent
    return (parent / target).as_posix().replace("./", "")


def descriptive_block(body: str) -> str:
    """The raw markdown of the page's own descriptive block. A page with neither a
    Description nor an Overview keeps its prose loose in the head above the first
    section, so that head is the block."""
    for heading in (DESCRIPTION, OVERVIEW):
        match = heading.search(body)
        if match:
            body = body[match.end():]
            break
    end = SECTION.search(body)
    return body[:end.start()] if end else body


def trivia_block(body: str) -> str:
    """The raw markdown of the page's Trivia section, empty when it has none. Unlike the
    descriptive block there is no fallback: a page either heads a section Trivia or Notes
    or it has no trivia."""
    for heading in (TRIVIA, NOTES):
        match = heading.search(body)
        if not match:
            continue
        body = body[match.end():]
        end = SECTION.search(body)
        return body[:end.start()] if end else body
    return ""


def render_heading(line: str):
    """A heading line as prose, or `None` where the heading is furniture.

    A subsection heading inside the block keeps its own wording as a bold line. Without it
    the paragraphs of two unrelated subsections run together into one wall of text. The
    page title, which is the only first-level heading a block can contain, and a heading
    the wiki left empty are furniture."""
    match = HEADING.match(line)
    if not match:
        return line
    depth, text = len(match.group(1)), match.group(2).strip()
    if depth < 3 or not text:
        return None
    return text if text.startswith("**") and text.endswith("**") else f"**{text}**"


def drop_table_intro(line: str) -> str:
    """A line with the sentence that introduced a removed table taken off it.

    The table is gone, so `The possible orders are as follow:` now introduces nothing.
    Returns the empty string when that sentence was the whole line."""
    if not line.rstrip().endswith(":"):
        return line
    return " ".join(SENTENCE.split(line.strip())[:-1])


def clean(page, page_id, text: str) -> str:
    """A block of a page as clean markdown, the prose kept verbatim.

    A wikilink becomes a token when the page it points at has a row, `[Etris](Etris.md)`
    -> `[Etris](location:etris)`, and its plain surface text when it does not. Everything
    the wiki wraps around the prose goes: tables, images, HTML, citation markers, the
    article banners and the page furniture. A bullet list stays a bullet list, nesting
    included, because a run of lines with no blank line between them is one paragraph."""
    text = HTML_TABLE.sub(f"\n{TABLE_MARK}\n", text)
    text = IMAGE.sub("", text)
    text = CITATION.sub("", text)

    def retarget(match):
        text, target = match.group(1), match.group(2)
        row = page_id.get(resolve_link(page, target))
        return f"[{text}]({row})" if row else text

    text = LINK.sub(retarget, text)
    text = LINE_BREAK.sub(" ", text)
    text = HTML_TAG.sub("", text)
    text = html.unescape(text).replace("\u00a0", " ")

    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = TABLE_MARK
        if TABLE_MARK in stripped:
            # The sentence above a table only makes sense with the table under it.
            last = next((i for i, kept in enumerate(reversed(lines)) if kept.strip()), None)
            if last is not None:
                at = len(lines) - 1 - last
                lines[at] = drop_table_intro(lines[at])
                if not lines[at].strip():
                    lines.pop(at)
            continue
        if FURNITURE.match(stripped):
            continue
        rendered = render_heading(line.rstrip())
        if rendered is not None:
            lines.append(rendered)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines))]
    paragraphs = [p for p in paragraphs if p and not PAGE_NOTICE.search(p)]

    # A subsection heading whose whole body was a table introduces nothing now.
    for at in reversed(range(len(paragraphs))):
        following = paragraphs[at + 1] if at + 1 < len(paragraphs) else None
        if BOLD_LINE.fullmatch(paragraphs[at]) and (following is None
                                                    or BOLD_LINE.fullmatch(following)):
            paragraphs.pop(at)

    # The mirror carries sections that are one empty bullet and nothing else, which cleans
    # down to punctuation. A block with no word in it is not prose.
    text = "\n\n".join(paragraphs)
    return text if any(ch.isalnum() for ch in text) else ""


def describe(page, page_id) -> str:
    """The page's own descriptive block, cleaned."""
    return clean(page, page_id, descriptive_block(page["body"]))


def trivia(page, page_id) -> str:
    """The page's Trivia section, cleaned. Empty when the page has none."""
    return clean(page, page_id, trivia_block(page["body"]))


# ---------------------------------------------------------------------- the three actions

Create = namedtuple("Create", "table key row rule")
AddRef = namedtuple("AddRef", "table key field id source")
# `field` is one of OWNED_FIELDS. Both are a wiki section transcribed verbatim, so the same
# action writes either: a desc is Set(..., "desc", ...), a trivia Set(..., "additional_info", ...).
Set = namedtuple("Set", "table key field value source")


def apply_create(action: Create):
    assert set(action.row) <= set(SKELETON_FIELDS), f"skeleton carries {set(action.row)}"
    assert action.table in TABLES
    path = REPO / action.table / f"{action.key}.json"
    assert not path.exists(), f"{path} already exists"
    path.write_text(json.dumps(action.row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_add_ref(action: AddRef):
    assert action.field in REF_FIELDS[action.table], f"{action.table} has no {action.field}"
    path = REPO / action.table / f"{action.key}.json"
    row = json.loads(path.read_text(encoding="utf-8"))

    before = list(row)
    prose = {field: row.get(field) for field in PROSE_FIELDS + OWNED_FIELDS}

    values = row.setdefault(action.field, [])
    if action.id in values:
        return False
    values.append(action.id)

    row = {field: row[field] for field in FIELD_ORDER if field in row}
    assert [f for f in row if f in before] == before, f"{path}: key order is not FIELD_ORDER"
    assert {field: row.get(field) for field in prose} == prose, f"{path}: prose changed"

    path.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def apply_set(action: Set):
    """Writes one owned field. Everything else the row carries, its key order, its refs, its
    hand-written fields and the tool's other owned field, has to come out unchanged."""
    assert action.field in OWNED_FIELDS, f"{action.field} is not the tool's to write"
    path = REPO / action.table / f"{action.key}.json"
    row = json.loads(path.read_text(encoding="utf-8"))

    before = list(row)
    untouched = PROSE_FIELDS + REF_FIELDS[action.table] \
        + tuple(f for f in OWNED_FIELDS if f != action.field)
    kept = {field: row.get(field) for field in untouched}

    if row.get(action.field) == action.value:
        return False
    row[action.field] = action.value

    row = {field: row[field] for field in FIELD_ORDER if field in row}
    assert [f for f in row if f in before] == before, f"{path}: key order is not FIELD_ORDER"
    assert {field: row.get(field) for field in untouched} == kept, \
        f"{path}: one of {list(untouched)} changed"

    path.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


# ------------------------------------------------------------------------------ the plan


def load_rows():
    return {
        table: {p.stem: json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((REPO / table).glob("*.json"))}
        for table in TABLES
    }


def canonical_groups(candidates, by_file):
    """The pages of one table, grouped by the wiki's own `Variants:` links. One group is
    one subject, so it gets one row."""
    in_table = {page["file"] for page in candidates}
    parent = {page["file"]: page["file"] for page in candidates}

    def find(f):
        while parent[f] != f:
            parent[f] = parent[parent[f]]
            f = parent[f]
        return f

    for page in candidates:
        for target in variant_targets(page, by_file):
            if target in in_table:
                a, b = find(page["file"]), find(target)
                if a != b:
                    parent[a] = b

    groups = defaultdict(list)
    for page in candidates:
        groups[find(page["file"])].append(page)
    return groups


class Resolver:
    """Wiki name -> row id, per table. A name two rows both claim resolves to nothing, so
    an ambiguous infobox cell becomes a report line instead of a wrong edge."""

    def __init__(self):
        self.index = {table: {} for table in TABLES}

    def claim(self, table, name, key):
        ident = name_to_identifier(name)
        if not ident or ident in PLACEHOLDERS:
            return
        held = self.index[table].get(ident, key)
        self.index[table][ident] = key if held == key else None

    def resolve(self, table, name):
        index = self.index[table]
        ident = name_to_identifier(name)
        if ident in PLACEHOLDERS:
            return None
        # An infobox cell drops or adds a leading "the" freely against the page title it
        # names, "Starswept Valley" for "The Starswept Valley". Try both spellings, and
        # take the answer only when one of them lands.
        other = ident[4:] if ident.startswith("the_") else f"the_{ident}"
        keys = {index.get(ident), index.get(other)} - {None}
        return f"{NAMESPACE[table]}:{keys.pop()}" if len(keys) == 1 else None


def plan(mirror, refresh_desc=False, refresh_trivia=False):
    pages = [p for p in (parse_page(f, mirror) for f in sorted(mirror.rglob("*.md"))) if p]
    by_file = {page["file"]: page for page in pages}
    rows = load_rows()

    skipped, candidates = [], defaultdict(list)
    for page in pages:
        table, rule = bucket(page)
        if not table:
            continue
        if page["title"] in HUB_PAGES:
            skipped.append((table, page["title"], "wiki index or mechanic page"))
            continue
        # An index page's subpages are what it indexes, so they follow it. They also share
        # a `Variants:` line that lists every sibling, which would fold them into one row.
        parent = page["title"].rsplit("/", 1)[0]
        if parent != page["title"] and parent in HUB_PAGES:
            skipped.append((table, page["title"], f"subpage of the {parent!r} index"))
            continue
        key = name_to_identifier(page["name"])
        bad = sorted(set(key) - KEY_CHARS)
        if bad:
            skipped.append((table, page["title"], f"key {key!r} has {bad}, no token can spell it"))
            continue
        page["key"], page["table"], page["rule"] = key, table, rule
        candidates[table].append(page)

    # One page per subject, and every page of a group pointing at the subject's row.
    resolver = Resolver()
    page_id = {}
    canonical = defaultdict(list)
    for table, group in candidates.items():
        existing = rows[table]
        for members in canonical_groups(group, by_file).values():
            # A page that already has a row wins, then the Pathfinder page, then order.
            members.sort(key=lambda p: (
                p["key"] not in existing,
                "(Vow of Iron)" in p["title"],
                "Pathfinder Exclusive" not in p["categories"],
                p["file"],
            ))
            head, variants = members[0], members[1:]
            head["variants"] = variants
            canonical[table].append(head)
            for variant in variants:
                skipped.append((table, variant["title"],
                                f"wiki lists it as a variant of {head['title']!r}"))
        canonical[table].sort(key=lambda p: p["file"])

    for table, existing in rows.items():
        for key, row in existing.items():
            resolver.claim(table, row["name"], key)
            resolver.claim(table, key, key)
            for alias in row.get("aliases", []):
                resolver.claim(table, alias, key)

    creates = []
    for table, group in canonical.items():
        kept = []
        for page in group:
            names = [page["name"]] + [v["name"] for v in page["variants"]]
            names += [DISAMBIGUATOR.sub("", n).strip() for n in names]

            # The page's own key wins over any name lookup. A name two rows both claim
            # resolves to nothing, and a page named exactly after a row is that row.
            claimed = ({f"{NAMESPACE[table]}:{page['key']}"} if page["key"] in rows[table]
                       else {resolver.resolve(table, n) for n in names} - {None})
            if len(claimed) > 1:
                skipped.append((table, page["title"], f"names split across rows {sorted(claimed)}"))
                continue
            kept.append(page)
            if claimed:
                key = claimed.pop().split(":", 1)[1]
                skipped.append((table, page["title"], f"{table}/{key} already covers it"))
            else:
                key = page["key"]
                aliases = []
                for alias in infobox_aliases(page) + [DISAMBIGUATOR.sub("", v["name"]).strip()
                                                      for v in page["variants"]]:
                    if name_to_identifier(alias) != key and alias not in aliases:
                        aliases.append(alias)
                row = {"name": page["name"]}
                if aliases:
                    row["aliases"] = aliases
                row["wiki"] = page["file"]
                creates.append(Create(table, key, row, page["rule"]))
                rows[table][key] = row
                for name in [page["name"]] + aliases:
                    resolver.claim(table, name, key)

            page["row_key"] = key
            page_id[page["file"]] = f"{NAMESPACE[table]}:{key}"
            for variant in page["variants"]:
                page_id[variant["file"]] = page_id[page["file"]]
        canonical[table] = kept

    refs, failures = derive_refs(canonical, resolver, page_id)
    adds = [ref for ref in refs
            if ref.id not in rows[ref.table][ref.key].get(ref.field, [])]
    unmatched = unmatched_refs(rows, refs)
    candidates_out = token_candidates(canonical, rows, page_id)
    sets, thin = derive_prose(rows, page_id, mirror, refresh_desc, refresh_trivia)
    return dict(pages=pages, rows=rows, canonical=canonical, creates=creates, adds=adds,
                failures=failures, unmatched=unmatched, tokens=candidates_out,
                descs=[s for s in sets if s.field == "desc"],
                trivia=[s for s in sets if s.field == "additional_info"],
                thin=thin, skipped=skipped)


def derive_prose(rows, page_id, mirror, refresh_desc, refresh_trivia):
    """The desc and the trivia every wiki-backed row should carry, and the desc extractions
    worth a look.

    A row's `wiki` field names the page it was written from, and that page is where both
    fields come from, whether or not the page is one the tool would file into a table
    itself. Without the matching refresh flag only a row that lacks the field is planned,
    so a rerun is a no-op. With it every row is planned, which is legal because both are
    the tool's fields. Most pages have no Trivia section, and that is not a problem worth
    reporting, so only desc feeds the thin list."""
    sets, thin = [], []
    for table, table_rows in rows.items():
        for key, row in table_rows.items():
            wiki = row.get("wiki")
            if not wiki:
                continue
            path = mirror / wiki
            page = parse_page(path, mirror) if path.is_file() else None
            if not page:
                thin.append((table, key, wiki, "no such page in the mirror"))
                continue

            desc = describe(page, page_id)
            if not desc:
                thin.append((table, key, wiki, "the page carries no prose"))
            elif len(desc) < SHORT_DESC:
                thin.append((table, key, wiki, f"{len(desc)} chars"))
            if desc and (refresh_desc or not row.get("desc")):
                sets.append(Set(table, key, "desc", desc, wiki))

            notes = trivia(page, page_id)
            if notes and (refresh_trivia or not row.get("additional_info")):
                sets.append(Set(table, key, "additional_info", notes, wiki))
    return sets, thin


def derive_refs(canonical, resolver, page_id):
    """Every structured ref the infoboxes state, and the names that resolved to no row.

    entity  `location`/`locations` -> found_at        entity `affiliation` -> member_of
    faction `leader`               -> the entity leads it
    faction `subunits`             -> the subunit is part_of it
    location `TERRITORY OF <name>` -> the location is part_of that faction
    """
    refs, failures = [], []

    def named(page, table, field, cell):
        for name in cell_names(cell):
            target = resolver.resolve(table, name)
            if target:
                yield name, target
            else:
                failures.append((page["title"], field, name, table))

    for page in canonical["entities"]:
        source = f"{page['file']} infobox"
        for field, cell in infobox(page):
            if field in ("location", "locations"):
                for _, target in named(page, "locations", field, cell):
                    refs.append(AddRef("entities", page["row_key"], "found_at", target, source))
            elif field == "affiliation":
                for _, target in named(page, "factions", field, cell):
                    refs.append(AddRef("entities", page["row_key"], "member_of", target, source))

    for page in canonical["factions"]:
        source = f"{page['file']} infobox"
        me = f"faction:{page['row_key']}"
        for field, cell in infobox(page):
            if field == "leader":
                for _, target in named(page, "entities", field, cell):
                    refs.append(AddRef("entities", target.split(":", 1)[1], "leads", me, source))
            elif field == "subunits":
                for _, target in named(page, "factions", field, cell):
                    refs.append(AddRef("factions", target.split(":", 1)[1], "part_of", me, source))

    for page in canonical["locations"]:
        match = TERRITORY.search(page["body"])
        if not match:
            continue
        source = f"{page['file']} TERRITORY OF"
        for _, target in named(page, "factions", "territory", match.group(1)):
            refs.append(AddRef("locations", page["row_key"], "part_of", target, source))

    return refs, failures


def unmatched_refs(rows, refs):
    """Refs a row carries that no infobox states. Hand-written context, not an error."""
    derived = defaultdict(set)
    for ref in refs:
        derived[(ref.table, ref.key, ref.field)].add(ref.id)

    out = []
    for table, fields in REF_FIELDS.items():
        for key, row in rows[table].items():
            for field in fields:
                for value in row.get(field, []):
                    if value not in derived[(table, key, field)]:
                        out.append((table, key, field, value))
    return out


def token_candidates(canonical, rows, page_id):
    """Rows that already have prose, and the ids their wiki page links to that the prose
    does not name yet. Report only: which mention deserves a token is a judgment call."""
    out = []
    for table, group in canonical.items():
        for page in group:
            row = rows[table][page["row_key"]]
            prose = json.dumps([row.get(f) for f in PROSE_FIELDS], ensure_ascii=False)
            if prose == json.dumps([None] * len(PROSE_FIELDS)):
                continue
            present = set(TOKEN.findall(prose)) | {page_id[page["file"]]}
            seen, found = set(), []
            for text, target in LINK.findall(page["body"]):
                target = page_id.get(resolve_link(page, target))
                if target and target not in present and target not in seen:
                    seen.add(target)
                    found.append((text.strip(), target))
            if found:
                out.append((table, page["row_key"], found))
    return out


# ----------------------------------------------------------------------------- reporting


def write_report(result, applied):
    rules = defaultdict(list)
    for create in result["creates"]:
        rules[create.rule].append(f"{create.table}/{create.key}")

    lines = ["# lore_sync report", ""]
    lines.append(f"mode: {'--apply' if applied else '--check'}")
    lines.append(f"pages read: {len(result['pages'])}")
    for table in TABLES:
        lines.append(f"{table}: {len(result['rows'][table])} rows, "
                     f"{len(result['canonical'][table])} wiki subjects")
    lines += ["", f"(a) rows to create: {len(result['creates'])}",
              f"(b) infobox refs missing from rows: {len(result['adds'])}",
              f"(c) refs no infobox derives (prose-derived, fine): {len(result['unmatched'])}",
              f"(d) name resolution failures: {len(result['failures'])}",
              f"(e) filled rows with untokenised wikilinks: {len(result['tokens'])}",
              f"(f) descs to write: {len(result['descs'])}",
              f"(g) trivia to write: {len(result['trivia'])}",
              f"(h) empty or very short desc extractions: {len(result['thin'])}",
              f"skipped pages: {len(result['skipped'])}", ""]

    lines += ["## (a) unregistered lore pages", ""]
    for rule, keys in sorted(rules.items()):
        lines.append(f"### {rule} — {len(keys)}")
        lines += [f"- {key}" for key in sorted(keys)] + [""]

    lines += ["## (b) infobox refs missing from rows", ""]
    for ref in sorted(result["adds"]):
        lines.append(f"- {ref.table}/{ref.key}: {ref.field} += {ref.id}  ({ref.source})")

    lines += ["", "## (c) refs no infobox derives — prose-derived, fine", ""]
    for table, key, field, value in sorted(result["unmatched"]):
        lines.append(f"- {table}/{key}: {field} has {value}")

    lines += ["", "## (d) name resolution failures", ""]
    for title, field, name, table in sorted(result["failures"]):
        lines.append(f"- {title}: {field} {name!r} matches no {table} row")

    lines += ["", "## (e) wikilink token candidates, per filled row", ""]
    for table, key, found in sorted(result["tokens"]):
        lines.append(f"### {table}/{key}")
        lines += [f"- [{text}]({target})" for text, target in found] + [""]

    lines += ["## (f) descs to write", ""]
    for action in sorted(result["descs"]):
        lines.append(f"- {action.table}/{action.key}: {len(action.value)} chars "
                     f"from {action.source}")

    lines += ["", "## (g) trivia to write", ""]
    for action in sorted(result["trivia"]):
        lines.append(f"- {action.table}/{action.key}: {len(action.value)} chars "
                     f"from {action.source}")

    lines += ["", "## (h) empty or very short desc extractions", ""]
    for table, key, file, reason in sorted(result["thin"]):
        lines.append(f"- {table}/{key}: {reason}  ({file})")

    lines += ["", "## skipped pages", ""]
    for table, title, reason in sorted(result["skipped"]):
        lines.append(f"- {table:10} {title!r}: {reason}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="create skeleton rows, add infobox refs and write descs and trivia")
    parser.add_argument("--refresh-desc", action="store_true",
                        help="plan a desc for every wiki-backed row, not only the empty ones")
    parser.add_argument("--refresh-trivia", action="store_true",
                        help="plan a trivia for every wiki-backed row, not only the empty ones")
    parser.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    args = parser.parse_args()

    if not args.mirror.is_dir():
        raise SystemExit(f"wiki mirror not found: {args.mirror}")

    result = plan(args.mirror, args.refresh_desc, args.refresh_trivia)
    if args.apply:
        for create in result["creates"]:
            apply_create(create)
        touched = {(ref.table, ref.key) for ref in result["adds"] if apply_add_ref(ref)}
        written = [action for action in result["descs"] + result["trivia"]
                   if apply_set(action)]
        print(f"created {len(result['creates'])} rows, "
              f"added {len(result['adds'])} refs across {len(touched)} rows, "
              f"wrote {sum(1 for a in written if a.field == 'desc')} descs and "
              f"{sum(1 for a in written if a.field == 'additional_info')} trivia")
        for table, key in sorted(touched):
            print(f"  ref {table}/{key}")
    else:
        for section, key in (("(a) create", "creates"), ("(b) missing refs", "adds"),
                             ("(c) prose-derived refs", "unmatched"),
                             ("(d) resolution failures", "failures"),
                             ("(e) token candidate rows", "tokens"),
                             ("(f) descs to write", "descs"),
                             ("(g) trivia to write", "trivia"),
                             ("(h) thin desc extractions", "thin")):
            print(f"{section:26} {len(result[key])}")

    write_report(result, args.apply)
    print(f"report: {REPORT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
