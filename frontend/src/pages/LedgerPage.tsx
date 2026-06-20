import { Fragment, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'react-bootstrap-icons';
import { Alert, Form, ProgressBar, Table } from 'react-bootstrap';

import { api, cents, centsToDollars, dollarsToCents, paymentClass, useAsync } from '../lib/api';

function assigneeNetCents(instance: any): number | null {
  if (instance.assignee_id == null) return null;
  const payment = instance.payments.find((p: any) => p.roommate_id === instance.assignee_id);
  // What the assignee nets for the chore (their transfer is negative when paid).
  return payment ? -payment.amount_cents : 0;
}

function paymentsTitle(instance: any): string {
  return instance.payments
    .map((payment: any) => `${payment.roommate_name} ${cents(payment.amount_cents)}`)
    .join('\n');
}

export default function LedgerPage({ refreshToken, bump }: { refreshToken: number; bump: () => void }) {
  const { data, setData, error } = useAsync(() => api('/api/ledger'), [refreshToken]);
  const [drafts, setDrafts] = useState<Record<number, any>>({});
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrolledRef = useRef(false);
  const focusIdRef = useRef<number | null>(null);

  const [showPrefs, setShowPrefs] = useState(false);
  const [openRoommates, setOpenRoommates] = useState<Set<number>>(() => new Set());
  const instances = data?.instances || [];
  const roommates = data?.roommates || [];
  const prefsByChore = data?.preferences_by_chore || {};

  const isOpen = (id: number) => openRoommates.has(id);
  function toggleRoommate(id: number) {
    setOpenRoommates((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  useEffect(() => {
    setDrafts(
      Object.fromEntries(
        instances.map((instance: any) => [
          instance.id,
          {
            name: instance.name,
            due_date: instance.due_date,
            assignee_id: instance.assignee_id == null ? '' : String(instance.assignee_id),
            payout: centsToDollars(instance.surplus_cents),
          },
        ]),
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Start scrolled to the bottom (newest weeks) on first load.
  useEffect(() => {
    if (data && scrollRef.current && !scrolledRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      scrolledRef.current = true;
    }
  }, [data]);

  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <div className="ledger-shell">Loading…</div>;

  function updateDraft(id: number, field: string, value: string) {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], [field]: value } }));
  }

  async function saveField(id: number, body: Record<string, unknown>) {
    const next = await api(`/api/ledger/instances/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    setData(next);
    bump();
  }

  async function deleteInstance(id: number) {
    const next = await api(`/api/ledger/instances/${id}`, { method: 'DELETE' });
    setData(next);
    bump();
  }

  async function addRow(targetWeek: string) {
    const existingIds = new Set(instances.map((instance: any) => instance.id));
    const next = await api('/api/ledger/instances', {
      method: 'POST',
      body: JSON.stringify({
        name: '',
        description: '',
        week_start: targetWeek,
        assignee_id: null,
        payout_cents: 0,
      }),
    });
    // Focus the freshly created row's name cell after it renders.
    const created = (next.instances || []).find((instance: any) => !existingIds.has(instance.id));
    focusIdRef.current = created ? created.id : null;
    setData(next);
    bump();
  }

  // Group instances by week so each week renders one merged cell. Always show
  // the current and upcoming weeks (even if empty) since they take add rows.
  const groupsByWeek = new Map<string, { week: string; rows: any[] }>();
  function ensureGroup(week: string) {
    if (!groupsByWeek.has(week)) groupsByWeek.set(week, { week, rows: [] });
    return groupsByWeek.get(week)!;
  }
  instances.forEach((instance: any) => ensureGroup(instance.week_start).rows.push(instance));
  ensureGroup(data.current_week);
  ensureGroup(data.upcoming_week);
  const groups = [...groupsByWeek.values()].sort((a, b) => a.week.localeCompare(b.week));

  const currentRows = groupsByWeek.get(data.current_week)!.rows;
  const currentDone = currentRows.filter((row) => row.status === 'done').length;
  const currentFailed = currentRows.filter((row) => row.status === 'failed').length;
  const currentTotal = currentRows.length;
  const weekStartDate = new Date(`${data.current_week}T00:00:00`);
  const msPerDay = 24 * 60 * 60 * 1000;
  const daysElapsed = (Date.now() - weekStartDate.getTime()) / msPerDay;
  const weekPct = Math.min(100, Math.max(0, Math.round((daysElapsed / 7) * 100)));

  // Bottom-level preference columns (also the colSpan of the WTP/BID banner).
  // Collapsed section = 1 edge column. Expanded = 2 columns per open roommate
  // and 1 per collapsed roommate.
  const prefCols = showPrefs
    ? roommates.reduce((acc: number, roommate: any) => acc + (isOpen(roommate.id) ? 2 : 1), 0)
    : 1;
  const addColSpan = 7 + prefCols;

  function dataCells(instance: any) {
    const draft = drafts[instance.id] || {};
    const net = assigneeNetCents(instance);
    return (
      <>
        <td className="nowrap">
          {instance.is_one_off ? (
            <Form.Control
              className="sheet-input"
              type="date"
              value={draft.due_date ?? instance.due_date}
              onChange={(event) => updateDraft(instance.id, 'due_date', event.target.value)}
              onBlur={() => saveField(instance.id, { due_date: draft.due_date })}
            />
          ) : (
            <span className="cell-static">{instance.due_date}</span>
          )}
        </td>
        <td>
          {instance.is_one_off ? (
            <>
              <Form.Control
                className="sheet-input"
                autoFocus={focusIdRef.current === instance.id}
                placeholder="Chore name…"
                value={draft.name ?? ''}
                onChange={(event) => updateDraft(instance.id, 'name', event.target.value)}
                onBlur={() => saveField(instance.id, { name: draft.name })}
              />
              <span className="tag">one-off</span>
            </>
          ) : (
            <span className="cell-static">{instance.name}</span>
          )}
        </td>
        {showPrefs ? (
          <>
            {roommates.map((roommate: any) => {
              if (!isOpen(roommate.id)) {
                return (
                  <td
                    key={roommate.id}
                    className="prefs-roommate-edge-cell"
                    onClick={() => toggleRoommate(roommate.id)}
                    title="Expand"
                  />
                );
              }
              const pref = (prefsByChore[instance.recurring_chore_id] || {})[roommate.id];
              return (
                <Fragment key={roommate.id}>
                  <td className="num pref-cell pref-cell-start">{pref ? cents(pref.wtp_cents) : '—'}</td>
                  <td className="num pref-cell pref-cell-end">{pref ? cents(pref.bid_cents) : '—'}</td>
                </Fragment>
              );
            })}
          </>
        ) : (
          <td className="prefs-edge" onClick={() => setShowPrefs(true)} title="Show WTP / Bid" />
        )}
        <td>
          {instance.is_one_off ? (
            <Form.Select
              className="sheet-input"
              value={draft.assignee_id ?? ''}
              onChange={(event) => {
                updateDraft(instance.id, 'assignee_id', event.target.value);
                saveField(instance.id, {
                  assignee_id: event.target.value ? Number(event.target.value) : null,
                });
              }}
            >
              <option value="">Unassigned</option>
              {roommates.map((roommate: any) => (
                <option key={roommate.id} value={roommate.id}>
                  {roommate.name}
                </option>
              ))}
            </Form.Select>
          ) : (
            <span className="cell-static" title="Auto-assigned from bids">
              {instance.assignee_name || '—'}
            </span>
          )}
        </td>
        <td className="num" title={paymentsTitle(instance)}>
          {instance.is_one_off ? (
            <Form.Control
              className="sheet-input money-input"
              type="number"
              min="0"
              step="0.01"
              value={draft.payout ?? '0.00'}
              onChange={(event) => updateDraft(instance.id, 'payout', event.target.value)}
              onBlur={() => saveField(instance.id, { payout_cents: dollarsToCents(draft.payout) })}
            />
          ) : net == null ? (
            ''
          ) : (
            <span className={paymentClass(net)}>{cents(net)}</span>
          )}
        </td>
        <td className="text-center">
          <Form.Check
            type="checkbox"
            checked={instance.status === 'done'}
            onChange={(event) =>
              saveField(instance.id, { status: event.target.checked ? 'done' : 'pending' })
            }
          />
        </td>
        <td className="text-center">
          <Form.Check
            type="checkbox"
            checked={instance.status === 'failed'}
            onChange={(event) =>
              saveField(instance.id, { status: event.target.checked ? 'failed' : 'pending' })
            }
          />
        </td>
        <td className="text-center">
          <button
            type="button"
            className="row-delete"
            title="Delete row"
            onClick={() => deleteInstance(instance.id)}
          >
            ×
          </button>
        </td>
      </>
    );
  }

  function weekCellClass(week: string) {
    if (week === data.current_week) return 'wk-current';
    if (week === data.upcoming_week) return 'wk-upcoming';
    return 'wk-other';
  }

  function renderGroup(group: { week: string; rows: any[] }) {
    const addable = group.week === data.current_week || group.week === data.upcoming_week;
    const span = group.rows.length + (addable ? 1 : 0);
    const isCurrent = group.week === data.current_week;
    const weekCell = (
      <td rowSpan={span} className={`week-cell ${weekCellClass(group.week)}`}>
        <div className="week-date">{group.week}</div>
        {isCurrent ? <span className="week-flag">This week</span> : null}
        {group.week === data.upcoming_week ? <span className="week-flag">Next week</span> : null}
        {isCurrent ? (
          <div className="week-progress">
            <ProgressBar now={weekPct} label={`${weekPct}%`} variant="success" />
            <div className="week-progress-label">
              {currentDone}/{currentTotal} done{currentFailed ? ` · ${currentFailed} failed` : ''}
            </div>
          </div>
        ) : null}
      </td>
    );

    const trs = group.rows.map((instance, index) => (
      <tr key={instance.id} className={instance.status === 'done' || instance.status === 'failed' ? 'is-done' : ''}>
        {index === 0 ? weekCell : null}
        {dataCells(instance)}
      </tr>
    ));

    if (addable) {
      trs.push(
        <tr key={`add-${group.week}`} className="add-row">
          {group.rows.length === 0 ? weekCell : null}
          <td colSpan={addColSpan}>
            <button type="button" className="add-row-btn" onClick={() => addRow(group.week)}>
              + Add chore to {isCurrent ? 'this week' : 'next week'}
            </button>
          </td>
        </tr>,
      );
    }
    return <tbody key={group.week} className="week-group">{trs}</tbody>;
  }

  return (
    <div className="ledger-shell">
      <div className="ledger-scroll" ref={scrollRef}>
        <Table bordered size="sm" className="align-middle ledger-table sheet mb-0">
          <thead>
            <tr>
              <th rowSpan={3}>Week</th>
              <th rowSpan={3}>Due</th>
              <th rowSpan={3}>Chore</th>
              {showPrefs ? (
                <th
                  colSpan={prefCols}
                  className="prefs-section-th"
                  title="Collapse WTP / Bid"
                  onClick={() => setShowPrefs(false)}
                >
                  <span className="prefs-label">
                    <ChevronRight size={11} />
                    WTP/BID
                    <ChevronLeft size={11} />
                  </span>
                </th>
              ) : (
                <th
                  rowSpan={3}
                  className="prefs-edge-th"
                  title="Show WTP / Bid"
                  onClick={() => setShowPrefs(true)}
                >
                  <span className="prefs-label collapsed">
                    <ChevronLeft size={11} />
                    WTP/BID
                    <ChevronRight size={11} />
                  </span>
                </th>
              )}
              <th rowSpan={3}>Assignee</th>
              <th rowSpan={3} className="num">Payout</th>
              <th rowSpan={3} className="text-center">Done</th>
              <th rowSpan={3} className="text-center">Failed</th>
              <th rowSpan={3} aria-label="delete"></th>
            </tr>
            <tr>
              {showPrefs
                ? roommates.map((roommate: any) =>
                    isOpen(roommate.id) ? (
                      <th
                        key={roommate.id}
                        colSpan={2}
                        className="prefs-group"
                        title="Collapse"
                        onClick={() => toggleRoommate(roommate.id)}
                      >
                        <span className="prefs-label">
                          <ChevronRight size={11} />
                          {roommate.name}
                          <ChevronLeft size={11} />
                        </span>
                      </th>
                    ) : (
                      <th
                        key={roommate.id}
                        rowSpan={2}
                        className="prefs-roommate-edge"
                        title="Expand"
                        onClick={() => toggleRoommate(roommate.id)}
                      >
                        <span className="prefs-label collapsed">
                          <ChevronLeft size={11} />
                          {roommate.name}
                          <ChevronRight size={11} />
                        </span>
                      </th>
                    ),
                  )
                : null}
            </tr>
            <tr>
              {showPrefs
                ? roommates
                    .filter((roommate: any) => isOpen(roommate.id))
                    .map((roommate: any) => (
                      <Fragment key={roommate.id}>
                        <th className="num pref-sub pref-cell-start">WTP</th>
                        <th className="num pref-sub pref-cell-end">Bid</th>
                      </Fragment>
                    ))
                : null}
            </tr>
          </thead>
          {groups.map(renderGroup)}
        </Table>
      </div>
    </div>
  );
}
