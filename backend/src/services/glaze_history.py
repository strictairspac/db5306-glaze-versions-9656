"""Committed glaze revisions ordered by the workshop's logical commit sequence.

A formula (``formula_id``) is the durable entity for the whole ledger.  The
human-facing glaze number (``釉号``) is a label that can move between formulas
through atomic renumbering batches.  A formula keeps its number until an
explicit batch gives it away, including while the formula is withdrawn.

A snapshot resolves a number against its own read watermark, so a number
observed at commit 30 still points at the formula that held it at commit 30
after a later transfer.  Withdrawal also follows the original formula: an old
snapshot that reads the number sees the withdrawal of the formula it belonged
to at that watermark, never the new holder.

Renumbering never moves version rows.  Versions before and after a number
change stay on the chain of their ``formula_id``, so reclamation works per
formula and cannot remove a version that an active snapshot still reads.  A
number that is only reachable through an old watermark therefore never loses
its version.
"""

from bisect import bisect_right
from dataclasses import dataclass, field


MAX_SEQUENCE = 2**53 - 1

# Canonical names first; aliases keep exported/older payloads readable.
_RENUMBER_KEYS = ("renumbers", "renumberings", "renumber_batches", "换号批次")
_LOOKUP_KEYS = ("lookups", "number_lookups", "number_queries", "queries", "釉号试查")


@dataclass(frozen=True, slots=True)
class Version:
    commit_seq: int
    recipe: str | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: str
    read_seq: int


@dataclass(frozen=True, slots=True)
class RenumberChange:
    formula_id: str
    glaze_no: str | None


@dataclass(frozen=True, slots=True)
class Renumber:
    commit_seq: int
    changes: tuple[RenumberChange, ...]


@dataclass(frozen=True, slots=True)
class NumberEvent:
    commit_seq: int
    formula_id: str
    before: str | None
    after: str | None
    # "登记" for a row in a renumber batch; "让号" when a withdrawn holder
    # implicitly releases its number to the receiving formula.
    source: str
    batch_members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Lookup:
    glaze_no: str
    read_seq: int


@dataclass(frozen=True, slots=True)
class Ledger:
    head_seq: int
    snapshots: list[Snapshot]
    chains: dict[str, list[Version]]
    renumbers: list[Renumber] = field(default_factory=list)
    number_events: list[NumberEvent] = field(default_factory=list)
    # formula_id -> sorted [(commit_seq, glaze_no | None)]
    number_changes: dict[str, list[tuple[int, str | None]]] = field(default_factory=dict)
    lookups: list[Lookup] = field(default_factory=list)

    @property
    def watermark(self) -> int:
        return self.snapshots[0].read_seq if self.snapshots else self.head_seq

    @property
    def renumber_change_count(self) -> int:
        return sum(len(batch.changes) for batch in self.renumbers)


def parse_ledger(payload: dict) -> Ledger:
    if not isinstance(payload, dict):
        raise ValueError("输入必须是对象")
    head = _sequence(payload.get("head_seq"), "已提交水位", 0, MAX_SEQUENCE)

    snapshots: list[Snapshot] = []
    snapshot_ids: set[str] = set()
    for row_number, row in enumerate(_rows(payload, "snapshots", "活跃快照"), 1):
        label = f"活跃快照第 {row_number} 行"
        snapshot_id = _required_text(row.get("snapshot_id"), f"{label}的快照编号")
        if snapshot_id in snapshot_ids:
            raise ValueError(f"快照编号重复：{snapshot_id}")
        read_seq = _sequence(row.get("read_seq"), f"{label}的读取水位", 0, head)
        snapshots.append(Snapshot(snapshot_id, read_seq))
        snapshot_ids.add(snapshot_id)

    versions: dict[str, dict[int, Version]] = {}
    for row_number, row in enumerate(_rows(payload, "versions", "配方版本"), 1):
        label = f"配方版本第 {row_number} 行"
        formula_id = _required_text(row.get("formula_id"), f"{label}的配方编号")
        commit_seq = _sequence(row.get("commit_seq"), f"{label}的提交序号", 1, head)
        operation = row.get("operation")
        recipe = row.get("recipe")
        if operation == "写入":
            recipe = _required_text(recipe, f"{label}的釉料配方")
        elif operation == "删除":
            if recipe is not None and (not isinstance(recipe, str) or recipe.strip()):
                raise ValueError(f"{label}是删除标记，釉料配方须留空")
            recipe = None
        else:
            raise ValueError(f"{label}的操作须为写入或删除")

        chain = versions.setdefault(formula_id, {})
        if commit_seq in chain:
            raise ValueError(f"配方 {formula_id} 的提交序号 {commit_seq} 重复")
        chain[commit_seq] = Version(commit_seq, recipe)

    snapshots.sort(key=lambda snapshot: (snapshot.read_seq, snapshot.snapshot_id))
    chains = {
        formula_id: sorted(chain.values(), key=lambda version: version.commit_seq)
        for formula_id, chain in sorted(versions.items())
    }
    renumbers = _parse_renumbers(payload, head)
    number_events = _validate_renumbers(chains, renumbers)
    number_changes = _number_changes(number_events)
    lookups = _parse_lookups(payload, head, snapshots)
    return Ledger(head, snapshots, chains, renumbers, number_events, number_changes, lookups)


# ---------------------------------------------------------------------------
# Renumber input parsing


def _parse_renumbers(payload: dict, head: int) -> list[Renumber]:
    """Read renumber rows; rows sharing a ``commit_seq`` form one atomic batch.

    Both flat rows (``{commit_seq, formula_id, glaze_no}``) and grouped rows
    (``{commit_seq, changes: [{formula_id, glaze_no}]}``) are accepted.
    """
    rows = _optional_rows(payload, _RENUMBER_KEYS, "换号批次")
    grouped: dict[int, list[RenumberChange]] = {}
    seen_formulas: dict[int, set[str]] = {}
    for row_number, row in enumerate(rows, 1):
        label = f"换号批次第 {row_number} 行"
        commit_seq = _sequence(_pick(row, "commit_seq", "提交序号"), f"{label}的提交序号", 1, head)
        raw_changes = _pick(row, "changes", "assignments", "moves", "换号")
        if raw_changes is None and any(key in row for key in ("formula_id", "配方编号", "glaze_no", "釉号")):
            raw_changes = [row]
        if not isinstance(raw_changes, list) or not raw_changes:
            raise ValueError(f"{label}至少包含一条换号")
        if any(not isinstance(change, dict) for change in raw_changes):
            raise ValueError(f"{label}的换号必须是对象数组")
        bucket = grouped.setdefault(commit_seq, [])
        formulas = seen_formulas.setdefault(commit_seq, set())
        for change_number, change in enumerate(raw_changes, 1):
            change_label = f"{label}第 {change_number} 条换号"
            formula_id = _required_text(_pick(change, "formula_id", "配方编号"), f"{change_label}的配方编号")
            if formula_id in formulas:
                raise ValueError(f"换号批次提交 {commit_seq} 对配方 {formula_id} 重复换号，整批退回")
            formulas.add(formula_id)
            bucket.append(RenumberChange(formula_id, _glaze_no(_pick(change, "glaze_no", "number", "釉号"), change_label)))

    return [
        Renumber(commit_seq, tuple(sorted(grouped[commit_seq], key=lambda change: change.formula_id)))
        for commit_seq in sorted(grouped)
    ]


def _glaze_no(value: object, label: str) -> str | None:
    """Blank or absent number means the formula gives its number up."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _required_text(value, f"{label}的釉号")


# ---------------------------------------------------------------------------
# Renumber validation


def _validate_renumbers(chains: dict[str, list[Version]], renumbers: list[Renumber]) -> list[NumberEvent]:
    """Apply batches atomically in commit order and reject any collision.

    A formula first appears with its own id as number; batches then move numbers
    between formulas.  All rows of one commit become visible together, so a
    swap is two rows in the same batch and the half-applied state never exists.
    A withdrawal follows the original formula, so a number held by a withdrawn
    formula may be handed to a receiver; the withdrawn holder implicitly
    releases it in the same batch.  A visible holder that is not part of the
    batch is a collision and the whole batch is rejected.  A number that still
    belongs to a formula which has not appeared yet is reserved for that
    formula's first version, so a batch may not take it early.
    """
    first_commit = {formula_id: chain[0].commit_seq for formula_id, chain in chains.items()}
    events: list[NumberEvent] = []
    state: dict[str, str | None] = {}

    batches = {batch.commit_seq: batch for batch in renumbers}
    commits = sorted(set(first_commit.values()) | set(batches))
    for commit_seq in commits:
        batch = batches.get(commit_seq)
        changed = {change.formula_id for change in batch.changes} if batch else set()
        # A formula that debuts in this very commit and is also renumbered in
        # the same batch takes the batch number directly; registering its own
        # id first would be a transient state that never becomes visible.
        for formula_id in sorted(first_commit):
            if first_commit[formula_id] != commit_seq or formula_id in state:
                continue
            if formula_id in changed:
                state[formula_id] = formula_id
                continue
            holder = next((other for other, number in state.items() if number == formula_id), None)
            if holder is not None:
                raise ValueError(
                    f"配方 {formula_id} 的首个版本与已让出的釉号 {formula_id} 撞号"
                    f"（当前由 {holder} 持有），请先修正换号批次")
            state[formula_id] = formula_id
        if batch is None:
            continue

        members = tuple(sorted(change.formula_id for change in batch.changes))
        for change in batch.changes:
            if change.formula_id not in chains:
                raise ValueError(
                    f"换号批次提交 {commit_seq} 的配方 {change.formula_id} 不在配方版本中，整批退回")
            if first_commit[change.formula_id] > commit_seq:
                raise ValueError(
                    f"换号批次提交 {commit_seq} 早于配方 {change.formula_id} 的首个版本，整批退回")

        future_ids = {
            formula_id for formula_id in chains
            if formula_id not in state and formula_id not in changed
        }
        for change in batch.changes:
            if change.glaze_no is not None and change.glaze_no in future_ids:
                raise ValueError(
                    f"换号批次提交 {commit_seq} 撞号：釉号 {change.glaze_no} 仍需留给配方 "
                    f"{change.glaze_no} 的首个版本，整批退回")

        target = dict(state)
        for change in batch.changes:
            target[change.formula_id] = change.glaze_no

        released: dict[str, NumberEvent] = {}
        for change in batch.changes:
            if change.glaze_no is None:
                continue
            for other, current in state.items():
                if other == change.formula_id or other in changed or current != change.glaze_no:
                    continue
                if _is_visible(chains[other], commit_seq):
                    raise ValueError(
                        f"换号批次提交 {commit_seq} 撞号：釉号 {change.glaze_no} 仍由可见配方 "
                        f"{other} 持有，整批退回")
                # The holder is already withdrawn: it releases the number and
                # the receiver can take it in this same atomic batch.
                target[other] = None
                released[other] = NumberEvent(commit_seq, other, current, None, "让号", members)

        holders: dict[str, str] = {}
        for formula_id in sorted(target):
            glaze_no = target[formula_id]
            if glaze_no is None:
                continue
            if glaze_no in holders:
                raise ValueError(
                    f"换号批次提交 {commit_seq} 撞号：釉号 {glaze_no} 同时属于 "
                    f"{holders[glaze_no]} 和 {formula_id}，整批退回")
            holders[glaze_no] = formula_id

        for change in batch.changes:
            before = state[change.formula_id]
            after = target[change.formula_id]
            if before != after:
                events.append(NumberEvent(commit_seq, change.formula_id, before, after, "登记", members))
        events.extend(released.values())
        state = target

    events.sort(key=lambda event: (event.commit_seq, event.formula_id, event.source))
    return events


def _number_changes(events: list[NumberEvent]) -> dict[str, list[tuple[int, str | None]]]:
    changes: dict[str, list[tuple[int, str | None]]] = {}
    for event in events:
        changes.setdefault(event.formula_id, []).append((event.commit_seq, event.after))
    for rows in changes.values():
        rows.sort(key=lambda row: row[0])
    return changes


def _is_visible(chain: list[Version], read_seq: int) -> bool:
    hit = version_at(chain, read_seq)
    return hit is not None and hit.recipe is not None


# ---------------------------------------------------------------------------
# Lookups


def _parse_lookups(payload: dict, head: int, snapshots: list[Snapshot]) -> list[Lookup]:
    snapshots_by_id = {snapshot.snapshot_id: snapshot for snapshot in snapshots}
    lookups: list[Lookup] = []
    for row_number, row in enumerate(_optional_rows(payload, _LOOKUP_KEYS, "釉号试查"), 1):
        label = f"釉号试查第 {row_number} 行"
        glaze_no = _required_text(_pick(row, "glaze_no", "number", "釉号"), f"{label}的釉号")
        raw_seq = _pick(row, "read_seq", "seq", "读取水位", "水位")
        if raw_seq is None and _pick(row, "snapshot_id", "快照编号") is not None:
            snapshot_id = _required_text(_pick(row, "snapshot_id", "快照编号"), f"{label}的快照编号")
            snapshot = snapshots_by_id.get(snapshot_id)
            if snapshot is None:
                raise ValueError(f"{label}的快照编号 {snapshot_id} 不在活跃快照中")
            raw_seq = snapshot.read_seq
        read_seq = _sequence(raw_seq, f"{label}的读取水位", 0, head)
        lookups.append(Lookup(glaze_no, read_seq))
    lookups.sort(key=lambda lookup: (lookup.read_seq, lookup.glaze_no))
    return lookups


def glaze_no_at(ledger: Ledger, formula_id: str, read_seq: int) -> str | None:
    """The glaze number held by a formula at a read watermark.

    Before a formula's first version it has no registered number.  Otherwise it
    starts with its formula id and keeps that number -- including through a
    withdrawal -- until a renumber batch explicitly gives it away.
    """
    changes = ledger.number_changes.get(formula_id, [])
    boundary = bisect_right(changes, read_seq, key=lambda change: change[0])
    if boundary:
        return changes[boundary - 1][1]
    chain = ledger.chains.get(formula_id, [])
    if not chain or read_seq < chain[0].commit_seq:
        return None
    return formula_id


def formula_at_number(ledger: Ledger, glaze_no: str, read_seq: int) -> str | None:
    """The formula that a glaze number points at in a snapshot at ``read_seq``."""
    for formula_id in ledger.chains:
        if glaze_no_at(ledger, formula_id, read_seq) == glaze_no:
            return formula_id
    return None


def numbers_at(ledger: Ledger, read_seq: int) -> dict[str, str]:
    """All numbers in use at a read watermark, mapped to their formulas."""
    numbers = {}
    for formula_id in ledger.chains:
        glaze_no = glaze_no_at(ledger, formula_id, read_seq)
        if glaze_no is not None:
            numbers[glaze_no] = formula_id
    return numbers


def lookup_number(ledger: Ledger, glaze_no: str, read_seq: int) -> dict:
    """Resolve a glaze number against a snapshot watermark.

    The returned ``formula_id`` is the formula that held the number at that
    watermark, which can differ from the formula holding it today.
    """
    formula_id = formula_at_number(ledger, glaze_no, read_seq)
    if formula_id is None:
        return {"formula_id": None, "hit": None, "status": "号码未登记"}
    hit = version_at(ledger.chains[formula_id], read_seq)
    if hit is None:
        status = "无可见版本"
    elif hit.recipe is None:
        status = "已删除"
    else:
        status = "可见"
    return {"formula_id": formula_id, "hit": hit, "status": status}


# ---------------------------------------------------------------------------
# Shared parsing helpers


def _rows(payload: dict, key: str, label: str) -> list[dict]:
    rows = payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label}必须是对象数组")
    return rows


def _optional_rows(payload: dict, keys: tuple[str, ...], label: str) -> list[dict]:
    """Read an optional array, accepting a few exported aliases.

    Archives produced before renumbering existed simply omit the key, which is
    valid and yields an empty list, so an old precheck still runs unchanged.
    """
    key = next((name for name in keys if name in payload), None)
    if key is None:
        return []
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{label}必须是对象数组")
    return value


def _pick(row: dict, *keys: str) -> object:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}须为非空文本")
    return value.strip()


def _sequence(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}须为 {minimum} 至 {maximum} 的整数")
    return value


def _boundary(chain: list[Version], read_seq: int) -> int:
    return bisect_right(chain, read_seq, key=lambda version: version.commit_seq)


def version_at(chain: list[Version], read_seq: int) -> Version | None:
    boundary = _boundary(chain, read_seq)
    return chain[boundary - 1] if boundary else None


def collectible_prefix_size(chain: list[Version], watermark: int) -> int:
    boundary = _boundary(chain, watermark)
    if boundary and chain[boundary - 1].recipe is not None:
        return boundary - 1
    # A boundary delete can disappear with its predecessors, leaving the formula absent.
    return boundary


def visible_version(hit: Version | None) -> Version | None:
    return hit if hit is not None and hit.recipe is not None else None
