"""回放釉料配方读快照，输出按配方归组的历史回收预检。

换号批次只改变配方的釉号归属，不改变配方版本链；因此回收仍按配方整批
计算，旧水位打开的快照也能借自己的水位解析出当时持有该釉号的配方。
"""

import copy

from .glaze_history import (
    Version,
    collectible_prefix_size,
    formula_at_number,
    glaze_no_at,
    lookup_number,
    parse_ledger,
    version_at,
    visible_version,
)


SPEC = {
    "name": "瓷汀工场·窑红试样册回收预检",
    "summary": "核对试烧和质检快照所见配方，并为版次管理员生成安全回收批单。",
    "customer": "瓷汀工场·釉料配方室与烧成质检组",
    "entity_labels": ["配方版本台账", "读快照回收预检", "留存批单"],
    "field_labels": {
        "head_seq": "已提交水位",
        "snapshots": "活跃快照",
        "versions": "配方版本",
        "renumbers": "换号批次",
        "changes": "换号",
        "lookups": "釉号试查",
        "snapshot_id": "快照编号",
        "read_seq": "读取水位",
        "formula_id": "配方编号",
        "commit_seq": "提交序号",
        "operation": "版本操作",
        "recipe": "釉料配方",
        "glaze_no": "釉号",
    },
    "guidance": (
        "从同一批台账录入已提交水位、配方版本和仍需复读的活跃快照。"
        "操作填「写入」或「删除」，删除行的配方留空。按配方整批复核回收前缀。"
        "换号批次以提交序号整批切换，同批可让号、受让或互换；撞号会整批退回。"
        "釉号试查填写釉号和读取水位，按该水位解析持有配方与命中的版次。"
    ),
    "sample": {
        "head_seq": 90,
        "snapshots": [
            {"snapshot_id": "质检-复核", "read_seq": 45},
            {"snapshot_id": "试烧-称料", "read_seq": 30},
            {"snapshot_id": "窑务-入窑", "read_seq": 65},
        ],
        "versions": [
            {"formula_id": "GL-Q01", "commit_seq": 10, "operation": "写入", "recipe": "青岚釉｜长石 62 g、石英 28 g、高岭土 10 g"},
            {"formula_id": "GL-Q01", "commit_seq": 30, "operation": "写入", "recipe": "青岚釉｜长石 60 g、石英 30 g、高岭土 10 g"},
            {"formula_id": "GL-Q01", "commit_seq": 40, "operation": "删除", "recipe": ""},
            {"formula_id": "GL-Q01", "commit_seq": 60, "operation": "写入", "recipe": "青岚釉｜长石 58 g、石英 32 g、高岭土 10 g"},
            {"formula_id": "GL-Q01", "commit_seq": 80, "operation": "写入", "recipe": "青岚釉｜长石 56 g、石英 34 g、高岭土 10 g"},
            {"formula_id": "GL-B02", "commit_seq": 12, "operation": "写入", "recipe": "白砂釉｜长石 50 g、石英 35 g、高岭土 15 g"},
            {"formula_id": "GL-B02", "commit_seq": 24, "operation": "删除", "recipe": ""},
            {"formula_id": "GL-H03", "commit_seq": 5, "operation": "写入", "recipe": "褐砂釉｜长石 66 g、石英 22 g、高岭土 12 g"},
            {"formula_id": "GL-H03", "commit_seq": 18, "operation": "写入", "recipe": "褐砂釉｜长石 64 g、石英 24 g、高岭土 12 g"},
            {"formula_id": "GL-H03", "commit_seq": 36, "operation": "写入", "recipe": "褐砂釉｜长石 62 g、石英 26 g、高岭土 12 g"},
            {"formula_id": "GL-H03", "commit_seq": 72, "operation": "删除", "recipe": ""},
            {"formula_id": "GL-M04", "commit_seq": 58, "operation": "写入", "recipe": "蜜灰釉｜长石 55 g、石英 25 g、高岭土 20 g"},
        ],
        "renumbers": [
            {
                "commit_seq": 76,
                "changes": [
                    {"formula_id": "GL-H03", "glaze_no": ""},
                    {"formula_id": "GL-M04", "glaze_no": "GL-H03"},
                ],
            },
            {
                "commit_seq": 82,
                "changes": [
                    {"formula_id": "GL-Q01", "glaze_no": "GL-H03"},
                    {"formula_id": "GL-M04", "glaze_no": "GL-Q01"},
                ],
            },
        ],
        "lookups": [
            {"glaze_no": "GL-Q01", "read_seq": 30},
            {"glaze_no": "GL-Q01", "read_seq": 90},
            {"glaze_no": "GL-H03", "read_seq": 65},
            {"glaze_no": "GL-H03", "read_seq": 90},
            {"glaze_no": "GL-B02", "read_seq": 30},
        ],
    },
    "expect": {"回收低水位": 30, "可回收版本数": 4, "保留版本数": 8, "当前可见配方数": 2},
}


def validate(payload):
    parse_ledger(payload)


def preview_renumber(payload, change, commit_seq):
    """Validate one batch against the ledger before the page registers it.

    The batch is applied on a copy of the ledger, so a collision rejects only
    this submission and never mutates the stored archive.  A commit sequence
    that already carries a batch is rejected rather than merged, so a batch
    always remains the single atomic unit that was registered.
    """
    ledger = parse_ledger(payload)
    if any(batch.commit_seq == commit_seq for batch in ledger.renumbers):
        raise ValueError(f"提交 {commit_seq} 已有换号批次，请换一个提交序号整批重新登记")
    change = _coerce_changes(change)
    validation = copy.deepcopy(payload)
    validation["renumbers"] = _renumber_payload_rows(payload) + [{
        "commit_seq": commit_seq,
        "changes": change,
    }]
    for alias in ("renumberings", "renumber_batches", "换号批次"):
        validation.pop(alias, None)
    newest = parse_ledger(validation)
    rows, timeline = _renumber_rows(newest)
    batch_rows = [row for row in rows if row["提交序号"] == commit_seq]
    return {
        "commit_seq": commit_seq,
        "changes": batch_rows,
        "timeline": timeline,
        "summary": f"提交 {commit_seq} 的换号批次可整批生效，共 {len(batch_rows)} 条换号。",
        "renumber_count": len(ledger.renumbers) + 1,
    }


def register_renumber(payload, change, commit_seq):
    """Return a payload with one validated atomic renumber batch appended."""
    preview_renumber(payload, change, commit_seq)
    updated = copy.deepcopy(payload)
    updated["renumbers"] = _renumber_payload_rows(payload) + [{
        "commit_seq": commit_seq,
        "changes": _coerce_changes(change),
    }]
    for alias in ("renumberings", "renumber_batches", "换号批次"):
        updated.pop(alias, None)
    parse_ledger(updated)
    return updated


def renumber_snapshot(payload, commit_seq):
    """The registered batch, as evidence that it took effect as a whole."""
    ledger = parse_ledger(payload)
    rows, _ = _renumber_rows(ledger)
    batch = [row for row in rows if row["提交序号"] == commit_seq]
    return {
        "commit_seq": commit_seq,
        "changes": batch,
        "summary": f"提交 {commit_seq} 的换号批次已整批登记，共 {len(batch)} 条换号。",
    }


def preview_lookup(payload, glaze_no, read_seq):
    """Resolve one glaze number at one watermark without persisting it."""
    ledger = parse_ledger(payload)
    resolved = lookup_number(ledger, glaze_no, read_seq)
    hit = resolved["hit"]
    return {
        "釉号": glaze_no,
        "读取水位": read_seq,
        "判定": resolved["status"],
        "命中配方编号": resolved["formula_id"],
        "命中提交序号": hit.commit_seq if hit is not None else None,
        "命中操作": ("删除" if hit.recipe is None else "写入") if hit is not None else None,
        "釉料配方": hit.recipe if hit is not None else None,
        "当前读持有配方": formula_at_number(ledger, glaze_no, ledger.head_seq),
        "回收低水位": ledger.watermark,
    }


def _renumber_payload_rows(payload):
    """Existing batches from the first recognized key, matching parse order."""
    for key in ("renumbers", "renumberings", "renumber_batches", "换号批次"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _coerce_changes(change):
    if isinstance(change, list):
        return change
    return [change]


def solve(payload, engine):
    """Simulate collection in the supplied ledger by removing planned prefixes."""
    ledger = parse_ledger(payload)
    watermark = ledger.watermark
    has_renumbers = bool(ledger.renumbers)
    reads = [
        ("活跃快照", snapshot.snapshot_id, snapshot.read_seq)
        for snapshot in ledger.snapshots
    ]
    reads.append(("当前读", "当前", ledger.head_seq))
    read_rows = []
    version_rows = []
    batch_rows = []
    collect_count = 0
    current_count = 0
    retained_chains: dict[str, list[Version]] = {}

    for formula_id, chain in ledger.chains.items():
        prefix_size = collectible_prefix_size(chain, watermark)
        retained = chain[prefix_size:]
        retained_chains[formula_id] = retained
        collect_count += prefix_size
        if prefix_size:
            last_candidate = chain[prefix_size - 1]
            batch_row = {
                "配方编号": formula_id,
                "批内最大提交序号": last_candidate.commit_seq,
                "可回收版本数": prefix_size,
                "含删除标记数": sum(version.recipe is None for version in chain[:prefix_size]),
                "执行口径": "按此配方整批移除列出的历史前缀",
            }
            if has_renumbers:
                batch_row["当前釉号"] = glaze_no_at(ledger, formula_id, ledger.head_seq)
                batch_row["换号批次"] = _renumber_commits(ledger, formula_id)
            batch_rows.append(batch_row)
        for index, version in enumerate(chain):
            eligible = index < prefix_size
            row = {
                "配方编号": formula_id,
                "提交序号": version.commit_seq,
                "版本操作": "删除" if version.recipe is None else "写入",
                "釉料配方": version.recipe,
                "判定": "可回收" if eligible else "保留",
                "依据": _collection_reason(chain, index, watermark, eligible),
            }
            if has_renumbers:
                # The version stays with the formula; the number is only a label
                # resolved at each read watermark.
                row["当时釉号"] = glaze_no_at(ledger, formula_id, version.commit_seq)
            version_rows.append(row)
        for read_type, snapshot_id, read_seq in reads:
            hit = version_at(chain, read_seq)
            after = version_at(retained, read_seq)
            visible = visible_version(hit)
            if visible != visible_version(after):
                raise RuntimeError(f"配方 {formula_id} 在读取水位 {read_seq} 的可见性发生变化")
            if read_type == "当前读" and visible is not None:
                current_count += 1
            row = {
                "配方编号": formula_id,
                "读取类型": read_type,
                "快照编号": snapshot_id,
                "读取水位": read_seq,
                "判定": _read_status(hit),
                "命中提交序号": hit.commit_seq if hit is not None else None,
                "釉料配方": visible.recipe if visible is not None else None,
                "回收后命中序号": after.commit_seq if after is not None else None,
                "前后可见配方一致": "是",
            }
            if has_renumbers:
                row["读取釉号"] = glaze_no_at(ledger, formula_id, read_seq)
            read_rows.append(row)

    lookup_rows = _lookup_rows(ledger, retained_chains, watermark)
    renumber_rows, timeline_rows = _renumber_rows(ledger)

    total_versions = len(version_rows)
    sources = [
        snapshot.snapshot_id for snapshot in ledger.snapshots if snapshot.read_seq == watermark
    ]
    summary = (
        f"回收低水位为 {watermark}，{len(batch_rows)} 个配方批次共 {collect_count} 条版本可回收。"
        f"已核对 {len(read_rows)} 条配方读取，模拟回收前后的可见配方一致。"
    )
    metrics = {
        "回收低水位": watermark,
        "活跃快照数": len(ledger.snapshots),
        "配方版本数": total_versions,
        "历史回收批次数": len(batch_rows),
        "可回收版本数": collect_count,
        "保留版本数": total_versions - collect_count,
        "当前可见配方数": current_count,
        "读取核对数": len(read_rows),
    }
    sections = [
        {"title": "回收低水位依据", "rows": [{
            "已提交水位": ledger.head_seq,
            "回收低水位": watermark,
            "活跃快照数": len(ledger.snapshots),
            "低水位来源": "最早活跃快照" if sources else "无活跃快照，取已提交水位",
            "限制回收的快照": "、".join(sources) if sources else None,
        }]},
    ]
    if has_renumbers or lookup_rows:
        summary += (
            f"已按水位复核 {len(lookup_rows)} 次釉号试查，"
            f"{len(ledger.renumbers)} 个换号批次整批生效。"
        )
        metrics["换号批次数"] = len(ledger.renumbers)
        metrics["换号条数"] = ledger.renumber_change_count
        metrics["试查数"] = len(lookup_rows)
        metrics["釉号变更配方数"] = len(ledger.number_changes)
        if has_renumbers:
            sections.append({"title": "换号批次与撞号校验", "rows": renumber_rows})
            sections.append({"title": "釉号归属时间线", "rows": timeline_rows})
    sections.extend([
        {"title": "配方读取与回收后复核", "rows": read_rows},
    ])
    if lookup_rows:
        sections.append({"title": "釉号试查与命中版次", "rows": lookup_rows})
    sections.extend([
        {"title": "逐版本留存依据", "rows": version_rows},
        {"title": "待核准回收批单", "rows": batch_rows},
    ])
    return {
        "summary": summary,
        "metrics": metrics,
        "sections": sections,
    }


def _lookup_rows(ledger, retained_chains, watermark):
    rows = []
    for lookup in ledger.lookups:
        resolved = lookup_number(ledger, lookup.glaze_no, lookup.read_seq)
        formula_id = resolved["formula_id"]
        hit = resolved["hit"]
        after = None
        after_status = None
        if formula_id is not None:
            after = version_at(retained_chains[formula_id], lookup.read_seq)
            after_status = _read_status(after)
        changed = visible_version(hit) != visible_version(after)
        if changed and lookup.read_seq >= watermark:
            # An active snapshot would read a different revision after collection.
            raise RuntimeError(
                f"釉号 {lookup.glaze_no} 在读取水位 {lookup.read_seq} 的可见版次会因回收变化")
        if formula_id is None:
            consistency = "未命中配方"
        elif changed:
            consistency = "否，低于回收低水位，命中可能变化"
        else:
            consistency = "是"
        row = {
            "釉号": lookup.glaze_no,
            "读取水位": lookup.read_seq,
            "判定": resolved["status"],
            "命中配方编号": formula_id,
            "命中提交序号": hit.commit_seq if hit is not None else None,
            "命中操作": ("删除" if hit.recipe is None else "写入") if hit is not None else None,
            "釉料配方": hit.recipe if hit is not None else None,
            "水位保护": "达到回收低水位" if lookup.read_seq >= watermark else "低于回收低水位",
            "回收后命中序号": after.commit_seq if after is not None else None,
            "回收后判定": after_status,
            "前后可见配方一致": consistency,
        }
        current_holder = formula_at_number(ledger, lookup.glaze_no, ledger.head_seq)
        row["当前读持有配方"] = current_holder
        rows.append(row)
    return rows


def _renumber_rows(ledger):
    rows = []
    timeline = []
    for batch in ledger.renumbers:
        labels = "、".join(change.formula_id for change in batch.changes)
        before_numbers = _numbers_before(ledger, batch.commit_seq)
        after_numbers = {change.formula_id: change.glaze_no for change in batch.changes}
        for change in batch.changes:
            before = before_numbers.get(change.formula_id)
            rows.append({
                "提交序号": batch.commit_seq,
                "同批配方": labels,
                "配方编号": change.formula_id,
                "换号前釉号": before,
                "换号后釉号": change.glaze_no,
                "换号动作": _renumber_action(
                    change.formula_id, before, change.glaze_no, before_numbers, after_numbers),
                "撞号校验": "通过，整批一次性生效",
            })
            timeline.append({
                "配方编号": change.formula_id,
                "生效提交序号": batch.commit_seq,
                "换号前釉号": before,
                "换号后釉号": change.glaze_no,
                "同批切换": labels,
            })
        # A withdrawn holder that was not listed still releases its number in
        # the same atomic batch; show it so the batch prefix can be reviewed.
        for event in ledger.number_events:
            if event.commit_seq != batch.commit_seq or event.source != "让号":
                continue
            rows.append({
                "提交序号": batch.commit_seq,
                "同批配方": labels,
                "配方编号": event.formula_id,
                "换号前釉号": event.before,
                "换号后釉号": None,
                "换号动作": "停用让号",
                "撞号校验": "通过，整批一次性生效",
            })
            timeline.append({
                "配方编号": event.formula_id,
                "生效提交序号": batch.commit_seq,
                "换号前釉号": event.before,
                "换号后釉号": None,
                "同批切换": labels,
            })
    return rows, timeline


def _numbers_before(ledger, commit_seq: int) -> dict[str, str | None]:
    numbers = {}
    for formula_id, chain in ledger.chains.items():
        before: str | None = None
        if chain and chain[0].commit_seq < commit_seq:
            before = formula_id
            for change_seq, glaze_no in ledger.number_changes.get(formula_id, []):
                if change_seq >= commit_seq:
                    break
                before = glaze_no
        numbers[formula_id] = before
    return numbers


def _renumber_action(
    formula_id: str,
    before: str | None,
    after: str | None,
    before_numbers: dict[str, str | None],
    after_numbers: dict[str, str | None],
) -> str:
    if after is None:
        return "让号停用"
    if before is None:
        return "受让启用"
    if after == before:
        return "续用"
    holder = next((f for f, number in before_numbers.items() if number == after and f != formula_id), None)
    if holder is not None and after_numbers.get(holder) == before:
        return "同批互换"
    if holder is not None:
        return "同批受让"
    return "换号"


def _renumber_commits(ledger, formula_id: str) -> str:
    commits = [str(seq) for seq, _ in ledger.number_changes.get(formula_id, [])]
    return "、".join(commits) if commits else "无"


def _read_status(hit: Version | None) -> str:
    if hit is None:
        return "无可见版本"
    if hit.recipe is None:
        return "已删除"
    return "可见"


def _collection_reason(chain: list[Version], index: int, watermark: int, eligible: bool) -> str:
    version = chain[index]
    if version.commit_seq > watermark:
        return f"提交序号高于低水位 {watermark}，保留该读取区间的变化"
    if not eligible:
        return f"低水位 {watermark} 命中的配方版次"
    if index + 1 < len(chain) and chain[index + 1].commit_seq <= watermark:
        return f"提交 {chain[index + 1].commit_seq} 已在低水位前或当时覆盖此版本"
    return f"低水位 {watermark} 命中删除标记，与此前的同配方历史一起回收"
