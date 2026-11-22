import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {topic} from '../constants/project.js';
import {esc} from '../utils/dom.js';

const EXAMPLE_CHANGES = JSON.stringify([
  {formula_id: 'GL-Q01', glaze_no: 'GL-H03'},
  {formula_id: 'GL-H03', glaze_no: 'GL-Q01'},
], null, 2);

function batches(payload) {
  const rows = [];
  if (!payload || typeof payload !== 'object') return rows;
  for (const key of ['renumbers', 'renumberings', 'renumber_batches', '换号批次']) {
    if (!Array.isArray(payload[key])) continue;
    for (const batch of payload[key]) {
      if (!batch || typeof batch !== 'object') continue;
      const changes = Array.isArray(batch.changes) ? batch.changes : [batch];
      for (const change of changes) {
        rows.push({
          commit_seq: batch.commit_seq,
          formula_id: change.formula_id,
          glaze_no: change.glaze_no,
          members: changes.map(row => row.formula_id).filter(Boolean).join('、') || '单条',
        });
      }
    }
    break;
  }
  return rows;
}

function snapshots(payload) {
  return Array.isArray(payload?.snapshots) ? payload.snapshots : [];
}

function batchRows(payload) {
  return batches(payload).map(row => [
    esc(row.commit_seq),
    esc(row.members),
    esc(row.formula_id),
    esc(row.glaze_no || '（让号停用）'),
  ]);
}

function lookupResult(result) {
  if (!result) return '';
  if (result.error) return `<p class="error">${esc(result.error)}</p>`;
  const hint = result['回收低水位'] !== undefined && result['读取水位'] < result['回收低水位']
    ? '<p class="note">该水位低于回收低水位，结果只作只读参考。</p>' : '';
  const fields = [
    ['釉号', result['釉号']],
    ['读取水位', result['读取水位']],
    ['判定', result['判定']],
    ['命中配方编号', result['命中配方编号'] ?? '—'],
    ['命中提交序号', result['命中提交序号'] ?? '—'],
    ['命中操作', result['命中操作'] ?? '—'],
    ['釉料配方', result['釉料配方'] ?? '—'],
    ['当前读持有配方', result['当前读持有配方'] ?? '—'],
    ['回收低水位', result['回收低水位'] ?? '—'],
  ];
  return hint + records(['项目', '结果'], fields.map(([label, value]) => [esc(label), esc(value)]));
}

export function renumbersPage(state, identity) {
  const selected = state.cases.find(row => row.id === identity) || state.cases[0];
  if (!selected) return heading(topic.modules[4], '先建立一份配方版本档案，再登记换号批次。');
  const payload = selected.payload || {};
  const head = payload.head_seq;
  const operator = state.session.role === 'operator';
  const rows = batchRows(payload);
  const watermarks = snapshots(payload).map(snapshot =>
    `<option value="${esc(snapshot.read_seq)}">${esc(snapshot.snapshot_id)} · 水位 ${esc(snapshot.read_seq)}</option>`).join('');
  const registered = new Set(batches(payload).map(row => row.commit_seq)).size;
  const register = operator ? panel('登记换号批次', `
    <form id="renumber-form" class="stack">
      <p class="note">同一提交序号的换号整批切换。glaze_no 留空表示让号停用；同批互换的两条须写在同一提交序号里。</p>
      <label><span>提交序号</span><input name="commit_seq" type="number" min="1" max="9007199254740991" step="1" required value="${esc(head ?? '')}" /></label>
      <label class="field data"><span>换号</span><small>JSON 数组：formula_id · glaze_no。</small>
        <textarea name="changes" rows="8" spellcheck="false" required>${esc(EXAMPLE_CHANGES)}</textarea></label>
      <div class="actions"><button>整批登记</button><span class="note">撞号会整批退回，档案保持不变。</span></div>
    </form>`)
    : panel('登记换号批次', '<p class="note">经办员可以登记换号批次。</p>');
  return `${heading(topic.modules[4], '停用的釉号可以让给别的配方；同一提交序号也能整批互换。', `<a class="secondary" href="#cases/${selected.id}">查看档案</a>`)}
    <div class="spread"><section aria-label="换号批次">
      ${panel('已登记换号批次', records(['提交序号', '同批配方', '配方编号', '釉号'], rows, '该档案还没有换号批次。'))}
    </section>
    <aside class="brief"><h2>${esc(selected.title)}</h2>
      <dl><dt>已提交水位</dt><dd>${esc(head ?? '—')}</dd><dt>换号批次</dt><dd>${esc(registered)}</dd></dl>
      <p class="note">快照按自己的水位认号，旧快照仍读到当时持有该釉号的配方及其版次。</p></aside></div>
    ${register}
    ${panel('按水位试查釉号', `
      <p class="note">按读取水位解析当时持有该釉号的配方，并给出命中的版次；低于回收低水位的水位只作只读参考。</p>
      <form id="lookup-form" class="stack">
        <label><span>釉号</span><input name="glaze_no" required maxlength="80" value="${esc(batches(payload)[0]?.glaze_no || 'GL-Q01')}" /></label>
        <label><span>读取水位</span><input name="read_seq" type="number" min="0" max="9007199254740991" step="1" required value="${esc(head ?? '')}" /></label>
        <label><span>快速选择快照水位</span><select id="lookup-snapshot"><option value="">自填水位</option>${watermarks}</select></label>
        <div class="actions"><button>试查命中版次</button></div>
      </form>
      <div id="lookup-result" aria-live="polite"></div>`)}`;
}

export function bindRenumbers(state, api, update, fail) {
  const caseId = Number(location.hash.split('/')[1]) || state.cases[0]?.id;
  const selected = state.cases.find(row => row.id === caseId) || state.cases[0];
  if (!selected) return;
  const snapshotSelect = document.getElementById('lookup-snapshot');
  snapshotSelect?.addEventListener('change', () => {
    if (snapshotSelect.value) document.querySelector('#lookup-form [name="read_seq"]').value = snapshotSelect.value;
  });

  document.getElementById('renumber-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter;
    if (button) button.disabled = true;
    try {
      let changes;
      try { changes = JSON.parse(form.elements.changes.value); }
      catch { throw new Error('换号须为合法的 JSON 数组。'); }
      if (!Array.isArray(changes) || !changes.length) throw new Error('换号至少包含一条。');
      const body = {commit_seq: Number(form.elements.commit_seq.value), changes};
      await api(`/cases/${selected.id}/renumbers/preview`, {method: 'POST', data: body});
      await api(`/cases/${selected.id}/renumbers`, {method: 'POST', data: body});
      await update();
    } catch (error) { fail(error); }
    finally { if (button) button.disabled = false; }
  });

  document.getElementById('lookup-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter;
    if (button) button.disabled = true;
    const target = document.getElementById('lookup-result');
    try {
      const result = await api(`/cases/${selected.id}/lookups/preview`, {method: 'POST', data: {
        glaze_no: form.elements.glaze_no.value.trim(),
        read_seq: Number(form.elements.read_seq.value),
      }});
      target.innerHTML = lookupResult(result);
    } catch (error) {
      target.innerHTML = lookupResult({error: error.message});
    } finally { if (button) button.disabled = false; }
  });
}
