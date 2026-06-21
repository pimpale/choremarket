import { Fragment, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'react-bootstrap-icons';
import { Alert, Button, Form, Modal, ProgressBar, Tab, Table, Tabs } from 'react-bootstrap';

import { api, cents, centsToDollars, dollarsToCents, paymentClass, useAsync } from '../lib/api';
import { ledgerForInstance, membersForWeek, type InstanceLedger, type Person, type RawInstance } from '../lib/mechanism';

// Shortest prefix of each name that's still unique among the others, e.g.
// ["Bob", "Rob", "Ronald"] -> ["B", "Rob", "Ron"].
function distinctivePrefixes(names: string[]): string[] {
  return names.map((name, i) => {
    let length = 1;
    while (length < name.length) {
      const candidate = name.slice(0, length);
      const conflict = names.some((other, j) => j !== i && other.slice(0, length) === candidate);
      if (!conflict) break;
      length += 1;
    }
    return name.slice(0, length);
  });
}

const CADENCES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'ad-hoc', label: 'Ad-hoc' },
];

export default function LedgerPage({ refreshToken, bump }: { refreshToken: number; bump: () => void }) {
  const { data, setData, error } = useAsync(() => api('/api/ledger'), [refreshToken]);
  const [drafts, setDrafts] = useState<Record<number, any>>({});
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrolledRef = useRef(false);
  const focusIdRef = useRef<number | null>(null);

  const [showPrefs, setShowPrefs] = useState(false);
  const [showMech, setShowMech] = useState(false);
  const [openRoommates, setOpenRoommates] = useState<Set<number>>(() => new Set());
  const [recurringModal, setRecurringModal] = useState<any>(null);
  const instances = data?.instances || [];
  // The server now serves every roommate (incl. departed ones, with membership
  // dates) so historical weeks compute correctly. `roommates` is the current
  // membership used for the sheet's columns/dropdowns; `allRoommates` (with
  // dates) feeds the economics, which filter participants per week.
  const allRoommates = data?.roommates || [];
  const roommates = allRoommates.filter((r: any) => r.active);
  const recurringChores = data?.recurring_chores || [];
  const prefsByChore = data?.preferences_by_chore || {};
  const prefsByInstance = data?.preferences_by_instance || {};
  const mechanism = data?.mechanism || 'agv';
  const people: Person[] = allRoommates.map((r: any) => ({
    id: r.id,
    name: r.name,
    joinDate: r.join_date,
    leaveDate: r.leave_date,
  }));
  const nameById = new Map<number, string>(allRoommates.map((r: any) => [r.id, r.name]));
  const roommateAbbrevs = distinctivePrefixes(roommates.map((r: any) => r.name));
  const roommateAbbrev = new Map<number, string>(
    roommates.map((r: any, i: number) => [r.id, roommateAbbrevs[i]]),
  );

  // The client owns the economics: derive each row's assignee/transfer/skipped
  // state live from the raw primitives instead of reading server-computed values.
  const rawOf = (instance: any): RawInstance => ({
    id: instance.id,
    recurring_chore_id: instance.recurring_chore_id,
    assignee_id: instance.assignee_id,
    status: instance.status,
    payout_cents: instance.payout_cents ?? 0,
    manual_override: Boolean(instance.manual_override),
    week_start: instance.week_start,
  });
  const ledgers = new Map<number, InstanceLedger>(
    instances.map((instance: any) => [
      instance.id,
      ledgerForInstance(rawOf(instance), people, prefsByChore, mechanism, prefsByInstance),
    ]),
  );
  const ledgerOf = (instance: any): InstanceLedger =>
    ledgers.get(instance.id) ?? { assigneeId: null, surplusCents: 0, worthDoing: true, payments: {}, displayStatus: 'pending' };

  function assigneeNetCents(instance: any): number | null {
    const ledger = ledgerOf(instance);
    if (ledger.assigneeId == null) return null;
    // What the assignee nets (their transfer is negative when paid).
    return -(ledger.payments[ledger.assigneeId] ?? 0);
  }

  function paymentsTitle(instance: any): string {
    const { payments } = ledgerOf(instance);
    return Object.entries(payments)
      .map(([id, amount]) => `${nameById.get(Number(id)) ?? id} ${cents(amount)}`)
      .join('\n');
  }

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
            description: instance.description,
            due_date: instance.due_date,
            assignee_id: instance.assignee_id == null ? '' : String(instance.assignee_id),
            payout: centsToDollars(instance.payout_cents ?? 0),
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

  function openOneOffModal(instance: any) {
    const draft = drafts[instance.id] || instance;
    setRecurringModal({
      instanceId: instance.id,
      activeKey: 'overwrite',
      query: '',
      newName: draft.name ?? instance.name ?? '',
      newDescription: draft.description ?? instance.description ?? '',
      newCadence: 'weekly',
      manualAssigneeId: instance.assignee_id == null ? '' : String(instance.assignee_id),
      manualPayout: centsToDollars(instance.payout_cents ?? 0),
    });
  }

  function openRecurringModal(instance: any) {
    const ledger = ledgerOf(instance);
    const currentPrice = ledger.assigneeId == null ? 0 : -(ledger.payments[ledger.assigneeId] ?? 0);
    setRecurringModal({
      kind: 'recurring',
      activeKey: 'sell',
      instanceId: instance.id,
      name: instance.name,
      // "Sell to roommate" pre-fills the current doer + current price.
      manualAssigneeId: ledger.assigneeId == null ? '' : String(ledger.assigneeId),
      manualPayout: centsToDollars(currentPrice),
    });
  }

  async function convertToOneOff(instanceId: number) {
    const next = await api(`/api/ledger/instances/${instanceId}/one-off`, { method: 'POST' });
    setRecurringModal(null);
    setData(next);
    bump();
  }

  async function saveField(id: number, body: Record<string, unknown>) {
    const next = await api(`/api/ledger/instances/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    setData(next);
    bump();
  }

  async function saveOneOffPreference(
    instanceId: number,
    roommateId: number,
    field: 'wtp_cents' | 'bid_cents',
    value: string,
  ) {
    const current = prefsByInstance[instanceId]?.[roommateId] ?? {};
    const next = await api('/api/ledger/instance-preferences', {
      method: 'PUT',
      body: JSON.stringify({
        chore_instance_id: instanceId,
        roommate_id: roommateId,
        wtp_cents: field === 'wtp_cents' ? (value.trim() ? dollarsToCents(value) : null) : current.wtp_cents,
        bid_cents: field === 'bid_cents' ? (value.trim() ? dollarsToCents(value) : null) : current.bid_cents,
      }),
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

  async function convertToRecurring(instanceId: number, recurringChoreId: number) {
    const next = await api(`/api/ledger/instances/${instanceId}/recurring`, {
      method: 'POST',
      body: JSON.stringify({ recurring_chore_id: recurringChoreId }),
    });
    setRecurringModal(null);
    focusIdRef.current = null;
    setData(next);
    bump();
  }

  async function convertToNewRecurring() {
    if (!recurringModal?.newName?.trim()) return;
    const next = await api(`/api/ledger/instances/${recurringModal.instanceId}/recurring/new`, {
      method: 'POST',
      body: JSON.stringify({
        name: recurringModal.newName,
        description: recurringModal.newDescription ?? '',
        cadence: recurringModal.newCadence ?? 'weekly',
      }),
    });
    setRecurringModal(null);
    focusIdRef.current = null;
    setData(next);
    bump();
  }

  async function saveManualOverride() {
    if (!recurringModal?.manualAssigneeId) return;
    const next = await api(`/api/ledger/instances/${recurringModal.instanceId}/manual-override`, {
      method: 'POST',
      body: JSON.stringify({
        assignee_id: Number(recurringModal.manualAssigneeId),
        payout_cents: dollarsToCents(recurringModal.manualPayout ?? '0'),
      }),
    });
    setRecurringModal(null);
    focusIdRef.current = null;
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

  const weekStartDate = new Date(`${data.current_week}T00:00:00`);
  const msPerDay = 24 * 60 * 60 * 1000;
  const daysElapsed = (Date.now() - weekStartDate.getTime()) / msPerDay;
  const weekPct = Math.min(100, Math.max(0, Math.round((daysElapsed / 7) * 100)));
  const recurringSearch = recurringModal?.query?.trim().toLowerCase() ?? '';
  const matchingRecurringChores = recurringChores.filter((chore: any) => {
    if (!recurringSearch) return true;
    return `${chore.name} ${chore.description} ${chore.cadence}`.toLowerCase().includes(recurringSearch);
  });

  // Bottom-level preference columns (also the colSpan of the WTP/BID banner).
  // Collapsed section = 1 edge column. Expanded = 2 columns per open roommate
  // and 1 per collapsed roommate.
  const prefCols = showPrefs
    ? roommates.reduce((acc: number, roommate: any) => acc + (isOpen(roommate.id) ? 2 : 1), 0)
    : 1;
  const mechanismColumns =
    mechanism === 'vcg'
      ? [
          { key: 'total-wtp', label: 'Total WTP' },
          { key: 'lowest-bid', label: 'Lowest bid' },
          { key: 'surplus', label: 'Surplus' },
          { key: 'second-lowest', label: '2nd bid' },
          { key: 'doer-paid', label: 'Doer paid' },
          { key: 'house', label: 'House' },
          ...roommates.map((roommate: any) => ({
            key: `clarke-${roommate.id}`,
            label: roommate.name,
          })),
        ]
      : [
          { key: 'total-wtp', label: 'Total WTP' },
          { key: 'lowest-bid', label: 'Lowest bid' },
          { key: 'surplus', label: 'Surplus' },
          { key: 'avg-value', label: 'Avg value' },
          ...roommates.map((roommate: any) => ({
            key: `transfer-${roommate.id}`,
            label: roommate.name,
          })),
        ];
  const mechanismGroups =
    mechanism === 'vcg'
      ? [
          { label: 'Inputs', colSpan: 3 },
          { label: 'VCG price', colSpan: 3 },
          ...(roommates.length ? [{ label: 'Clarke tax', colSpan: roommates.length }] : []),
        ]
      : [
          { label: 'Inputs', colSpan: 3 },
          { label: 'AGV share', colSpan: 1 },
          ...(roommates.length ? [{ label: 'Transfers', colSpan: roommates.length }] : []),
        ];
  const mechCols = showMech ? mechanismColumns.length : 1;
  // Fixed data columns (Due, Chore, Transfer, Done, Failed, delete),
  // plus the collapsible WTP/Bid and Mechanism blocks.
  const addColSpan = 6 + prefCols + mechCols;

  // Reconstruct the per-roommate bids/WTP for the chore from the prefs grid,
  // limited to the roommates who participate in this instance's week.
  function choreFinancials(instance: any) {
    const rawPrefs = instance.recurring_chore_id == null
      ? prefsByInstance[instance.id] || {}
      : prefsByChore[instance.recurring_chore_id] || {};
    const members = membersForWeek(people, instance.week_start);
    const rows = members.map((r) => ({
      id: r.id,
      name: r.name,
      wtp: rawPrefs[r.id]?.wtp_cents ?? 0,
      bid: rawPrefs[r.id]?.bid_cents ?? (instance.recurring_chore_id == null ? 100_000_000 : 0),
    }));
    const totalWtp = rows.reduce((sum: number, p: any) => sum + p.wtp, 0);
    const byBid = [...rows].sort((a, b) => a.bid - b.bid);
    return { people: rows, totalWtp, byBid };
  }

  function mechValueCell(
    key: string,
    value: React.ReactNode,
    opts: { tip: string; valueClass?: string; partyLabel?: string; subLabel?: string; start?: boolean } = { tip: '' },
  ) {
    return (
      <td
        key={key}
        className={`num mech-data-cell${opts.start ? ' mech-cell-start' : ''}`}
        title={opts.tip}
      >
        {opts.partyLabel ? <span className="mech-cell-party">{opts.partyLabel}</span> : null}
        <span className={`mech-cell-value ${opts.valueClass ?? ''}`}>{value}</span>
        {opts.subLabel ? <span className="mech-cell-party">{opts.subLabel}</span> : null}
      </td>
    );
  }

  function blankMechCell(key: string, start = false) {
    return mechValueCell(key, '—', { tip: '', valueClass: 'muted-value', start });
  }

  function hasOneOffPricingData(instance: any) {
    if (!instance.is_one_off || instance.manual_override) return true;
    const prefs = prefsByInstance[instance.id] || {};
    return Object.values(prefs).some((pref: any) => pref?.wtp_cents != null && pref?.bid_cents != null);
  }

  function mechanismCells(instance: any, ledger: InstanceLedger) {
    const wtpTip = "Sum of every roommate's willingness to pay for the chore getting done.";
    const lowestBidTip = 'The assignee is the lowest bidder — the cheapest person to do the chore.';

    if (instance.manual_override) {
      return (
        <td
          colSpan={mechCols}
          className="mech-data-cell mech-cell-start mech-oneoff-cell"
          title="Manual override: direct assignee and price, without WTP/Bid mechanism pricing."
        >
          Manual override
        </td>
      );
    }

    if (!hasOneOffPricingData(instance)) {
      return (
        <td
          colSpan={mechCols}
          className="mech-data-cell mech-cell-start mech-oneoff-cell"
          title="Enter both WTP and Bid for at least one roommate before mechanism pricing can be computed."
        >
          At least one roommate must enter both a BID and WTP
        </td>
      );
    }

    const { totalWtp, byBid } = choreFinancials(instance);
    const lowest = byBid[0];
    const paymentOf = (id: number) => ledger.payments[id] ?? 0;

    if (ledger.assigneeId == null) {
      const baseCells = [
        mechValueCell('total-wtp', cents(totalWtp), { tip: wtpTip, start: true }),
        lowest
          ? mechValueCell('lowest-bid', cents(lowest.bid), {
              partyLabel: `from ${lowest.name}`,
              tip: 'The cheapest bid to do the chore — what doing it would cost the household.',
            })
          : blankMechCell('lowest-bid'),
        mechValueCell('surplus', cents(ledger.surplusCents), {
          valueClass: 'pay',
          subLabel: 'skipped',
          tip: 'Skipped: total WTP is below the lowest bid, so this is the shortfall.',
        }),
      ];
      return [
        ...baseCells,
        ...mechanismColumns.slice(baseCells.length).map((column) => blankMechCell(column.key)),
      ];
    }

    const doer = byBid.find((p: any) => p.id === ledger.assigneeId) ?? lowest;
    const others = byBid.filter((p: any) => p.id !== ledger.assigneeId);
    const secondLowest = others[0];
    // House = whatever the roommate transfers don't cover (zero under AGV).
    const house = -Object.values(ledger.payments).reduce((sum: number, amount) => sum + amount, 0);

    if (mechanism === 'vcg') {
      const doerNet = -paymentOf(doer.id);
      return [
        mechValueCell('total-wtp', cents(totalWtp), { tip: wtpTip, start: true }),
        mechValueCell('lowest-bid', cents(doer.bid), { partyLabel: `from ${doer.name}`, tip: lowestBidTip }),
        mechValueCell('surplus', cents(ledger.surplusCents), {
          valueClass: paymentClass(-ledger.surplusCents),
          subLabel: ledger.surplusCents < 0 ? 'skipped' : undefined,
          tip: 'Value the household gains = total WTP minus the lowest bid.',
        }),
        secondLowest
          ? mechValueCell('second-lowest', cents(secondLowest.bid), {
              partyLabel: `from ${secondLowest.name}`,
              tip: 'Sets the doer’s pay: under VCG the doer is paid the second-lowest bid (the Vickrey price).',
            })
          : blankMechCell('second-lowest'),
        mechValueCell('doer-paid', cents(doerNet), {
          partyLabel: `to ${doer.name}`,
          valueClass: 'receive',
          tip: 'What the assignee receives — the second-lowest bid (capped at the value they uniquely unlock when the job is only barely worth doing).',
        }),
        mechValueCell('house', cents(house), {
          valueClass: paymentClass(house),
          tip: 'VCG is not budget-balanced: the house covers the doer’s pay minus any Clarke taxes. Positive = deficit.',
        }),
        ...roommates.map((roommate: any) => {
          const amount = paymentOf(roommate.id);
          if (roommate.id === ledger.assigneeId) return blankMechCell(`clarke-${roommate.id}`);
          return mechValueCell(`clarke-${roommate.id}`, cents(amount), {
            partyLabel: `from ${roommate.name}`,
            valueClass: amount > 0 ? 'pay' : undefined,
            tip:
              'Clarke (pivotal) tax: a non-doer pays only when their WTP is what tips the chore from “not worth doing” to “worth doing.” It is $0 whenever there is surplus WTP to spare, and helps fund the doer’s pay when it does bite.',
          });
        }),
      ];
    }

    // AGV: budget-balanced expected-externality redistribution.
    const avgValue = Math.round(ledger.surplusCents / Math.max(1, byBid.length));
    return (
      [
        mechValueCell('total-wtp', cents(totalWtp), { tip: wtpTip, start: true }),
        mechValueCell('lowest-bid', cents(doer.bid), { partyLabel: `from ${doer.name}`, tip: lowestBidTip }),
        mechValueCell('surplus', cents(ledger.surplusCents), {
          subLabel: ledger.surplusCents < 0 ? 'skipped' : undefined,
          tip: 'Value the household gains = total WTP minus the lowest bid. AGV shares this out.',
        }),
        mechValueCell('avg-value', cents(avgValue), {
          tip: 'The pay/receive threshold: a roommate whose value of the outcome (their WTP, minus their bid if they are the doer) is below this average is paid; those above it pay.',
        }),
        ...roommates.map((roommate: any) => {
          const amount = paymentOf(roommate.id);
          const receives = amount < 0;
          const displayAmount = receives ? -amount : amount;
          return mechValueCell(`transfer-${roommate.id}`, cents(displayAmount), {
            partyLabel:
              amount === 0
                ? roommate.name
                : `${receives ? 'to' : 'from'} ${roommate.name}`,
            valueClass: paymentClass(amount),
            tip:
              'AGV redistribution (positive pays, negative receives). Receiving transfers are shown as positive green amounts here. All transfers sum to $0.',
          });
        }),
      ]
    );
  }

  function dataCells(instance: any) {
    const draft = drafts[instance.id] || {};
    const ledger = ledgerOf(instance);
    const net = assigneeNetCents(instance);
    return (
      <>
        <td className="nowrap wk-edge">
          <Form.Control
            className="sheet-input"
            type="date"
            value={draft.due_date ?? instance.due_date}
            onChange={(event) => updateDraft(instance.id, 'due_date', event.target.value)}
            onBlur={() => saveField(instance.id, { due_date: draft.due_date })}
          />
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
              <button
                type="button"
                className="tag tag-button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => openOneOffModal(instance)}
              >
                {instance.manual_override ? 'manual override' : 'one-off'}
              </button>
            </>
          ) : (
            <>
              <span className="cell-static cell-static-block">{instance.name}</span>
              <button
                type="button"
                className="tag tag-button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => openRecurringModal(instance)}
              >
                recurring
              </button>
            </>
          )}
          {ledger.displayStatus === 'skipped' ? (
            <span
              className="tag tag-skipped"
              title="Total WTP is below the lowest bid, so it isn't worth doing under the current mechanism."
            >
              skipped: WTP &lt; bid
            </span>
          ) : null}
        </td>
        {showPrefs ? (
          <>
            {roommates.map((roommate: any) => {
              if (!isOpen(roommate.id)) {
                return (
                  <td
                    key={roommate.id}
                    className="prefs-roommate-edge-cell"
                  />
                );
              }
              const pref = instance.is_one_off
                ? (prefsByInstance[instance.id] || {})[roommate.id]
                : (prefsByChore[instance.recurring_chore_id] || {})[roommate.id];
              if (instance.manual_override) {
                return (
                  <Fragment key={roommate.id}>
                    <td className="num pref-cell pref-cell-start">—</td>
                    <td className="num pref-cell pref-cell-end">—</td>
                  </Fragment>
                );
              }
              if (instance.is_one_off) {
                return (
                  <Fragment key={roommate.id}>
                    <td className="num pref-cell pref-cell-start">
                      <Form.Control
                        className="sheet-input money-input pref-input"
                        type="number"
                        min="0"
                        step="0.01"
                        placeholder="—"
                        defaultValue={pref?.wtp_cents == null ? '' : centsToDollars(pref.wtp_cents)}
                        onBlur={(event) =>
                          saveOneOffPreference(instance.id, roommate.id, 'wtp_cents', event.currentTarget.value)
                        }
                      />
                    </td>
                    <td className="num pref-cell pref-cell-end">
                      <Form.Control
                        className="sheet-input money-input pref-input"
                        type="number"
                        min="0"
                        step="0.01"
                        placeholder="—"
                        defaultValue={pref?.bid_cents == null ? '' : centsToDollars(pref.bid_cents)}
                        onBlur={(event) =>
                          saveOneOffPreference(instance.id, roommate.id, 'bid_cents', event.currentTarget.value)
                        }
                      />
                    </td>
                  </Fragment>
                );
              }
              return (
                <Fragment key={roommate.id}>
                  <td className="num pref-cell pref-cell-start">{pref ? cents(pref.wtp_cents) : '—'}</td>
                  <td className="num pref-cell pref-cell-end">{pref ? cents(pref.bid_cents) : '—'}</td>
                </Fragment>
              );
            })}
          </>
        ) : (
          <td className="prefs-edge" />
        )}
        <td className="num after-prefs transfer-summary-cell" title={paymentsTitle(instance)}>
          {net == null ? (
            <span className="cell-static">—</span>
          ) : (
            <>
              <span className="transfer-summary-party">
                {net < 0 ? 'from' : 'to'} {nameById.get(ledger.assigneeId!)}
              </span>
              <span className={`transfer-summary-value ${net > 0 ? 'receive' : net < 0 ? 'pay' : ''}`}>
                {cents(Math.abs(net))}
              </span>
            </>
          )}
        </td>
        {showMech ? (
          mechanismCells(instance, ledger)
        ) : (
          <td className="mech-edge" />
        )}
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
          </div>
        ) : null}
      </td>
    );

    const trs = group.rows.map((instance, index) => {
      const status = ledgerOf(instance).displayStatus;
      return (
      <tr
        key={instance.id}
        className={
          status === 'done' || status === 'failed'
            ? 'is-done'
            : status === 'skipped'
              ? 'is-skipped'
              : ''
        }
      >
        {index === 0 ? weekCell : null}
        {dataCells(instance)}
      </tr>
      );
    });

    if (addable) {
      trs.push(
        <tr key={`add-${group.week}`} className="add-row">
          {group.rows.length === 0 ? weekCell : null}
          <td colSpan={addColSpan} className="wk-edge">
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
        <Table size="sm" className="align-middle ledger-table sheet mb-0">
          <thead>
            <tr>
              <th rowSpan={3}>Week</th>
              <th rowSpan={3} className="wk-edge">Due</th>
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
              <th rowSpan={3} className="num after-prefs">Transfer</th>
              {showMech ? (
                <th
                  colSpan={mechCols}
                  className="mech-section-th"
                  title="Hide mechanism detail"
                  onClick={() => setShowMech(false)}
                >
                  <span className="prefs-label">
                    <ChevronRight size={11} />
                    Mechanism
                    <ChevronLeft size={11} />
                  </span>
                </th>
              ) : (
                <th
                  rowSpan={3}
                  className="mech-edge-th"
                  title="Show mechanism detail"
                  onClick={() => setShowMech(true)}
                >
                  <span className="prefs-label collapsed">
                    <ChevronLeft size={11} />
                    MECH
                    <ChevronRight size={11} />
                  </span>
                </th>
              )}
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
                        title={`Expand (${roommate.name})`}
                        onClick={() => toggleRoommate(roommate.id)}
                      >
                        <span className="prefs-label collapsed">
                          <ChevronLeft size={11} />
                          {roommateAbbrev.get(roommate.id)}
                          <ChevronRight size={11} />
                        </span>
                      </th>
                    ),
                  )
                : null}
              {showMech
                ? mechanismGroups.map((group, index) => (
                    <th
                      key={group.label}
                      colSpan={group.colSpan}
                      className={`mech-group${index === 0 ? ' mech-cell-start' : ''}`}
                    >
                      {group.label}
                    </th>
                  ))
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
              {showMech
                ? mechanismColumns.map((column, index) => (
                    <th
                      key={column.key}
                      className={`num mech-sub${index === 0 ? ' mech-cell-start' : ''}`}
                    >
                      {column.label}
                    </th>
                  ))
                : null}
            </tr>
          </thead>
          {groups.map(renderGroup)}
        </Table>
      </div>
      <Modal show={!!recurringModal} onHide={() => setRecurringModal(null)} centered size="lg">
        <Modal.Header closeButton>
          <Modal.Title>
            {recurringModal?.kind === 'recurring' ? `Recurring chore: ${recurringModal?.name ?? ''}` : 'Configure chore row'}
          </Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {recurringModal?.kind === 'recurring' ? (
            <Tabs
              activeKey={recurringModal?.activeKey ?? 'sell'}
              onSelect={(key) =>
                setRecurringModal((current: any) =>
                  current ? { ...current, activeKey: key ?? 'sell' } : current,
                )
              }
              className="mb-3"
            >
              <Tab eventKey="sell" title="Sell to roommate">
                <p className="status-text">
                  Hand this week’s chore to a roommate for a fixed price (defaults to the current
                  mechanism price). It becomes a manual-override task, detached from the recurring schedule.
                </p>
                <Form.Group className="mb-3" controlId="sell-assignee">
                  <Form.Label>Sell to</Form.Label>
                  <Form.Select
                    value={recurringModal?.manualAssigneeId ?? ''}
                    onChange={(event) =>
                      setRecurringModal((current: any) =>
                        current ? { ...current, manualAssigneeId: event.target.value } : current,
                      )
                    }
                  >
                    <option value="">Select roommate…</option>
                    {roommates.map((roommate: any) => (
                      <option key={roommate.id} value={roommate.id}>
                        {roommate.name}
                      </option>
                    ))}
                  </Form.Select>
                </Form.Group>
                <Form.Group className="mb-3" controlId="sell-price">
                  <Form.Label>Price</Form.Label>
                  <Form.Control
                    type="number"
                    min="0"
                    step="0.01"
                    value={recurringModal?.manualPayout ?? '0.00'}
                    onChange={(event) =>
                      setRecurringModal((current: any) =>
                        current ? { ...current, manualPayout: event.target.value } : current,
                      )
                    }
                  />
                </Form.Group>
                <Button onClick={saveManualOverride} disabled={!recurringModal?.manualAssigneeId}>
                  Sell to roommate
                </Button>
              </Tab>
              <Tab eventKey="one-off" title="Convert to one-off task">
                <p className="status-text">
                  Detach this row from the recurring schedule into a plain one-off you can price with WTP/Bid.
                </p>
                <Button variant="outline-secondary" onClick={() => convertToOneOff(recurringModal.instanceId)}>
                  Convert to one-off task
                </Button>
              </Tab>
            </Tabs>
          ) : (
          <Tabs
            activeKey={recurringModal?.activeKey ?? 'overwrite'}
            onSelect={(key) =>
              setRecurringModal((current: any) =>
                current ? { ...current, activeKey: key ?? 'overwrite' } : current,
              )
            }
            className="mb-3"
          >
            <Tab eventKey="overwrite" title="Overwrite">
              <Form.Control
                autoFocus
                placeholder="Search recurring chores..."
                value={recurringModal?.query ?? ''}
                onChange={(event) =>
                  setRecurringModal((current: any) =>
                    current ? { ...current, query: event.target.value } : current,
                  )
                }
              />
              <div className="recurring-picker">
                {matchingRecurringChores.length ? (
                  matchingRecurringChores.map((chore: any) => (
                    <button
                      type="button"
                      key={chore.id}
                      className="recurring-picker-option"
                      onClick={() => recurringModal && convertToRecurring(recurringModal.instanceId, chore.id)}
                    >
                      <span className="recurring-picker-heading">
                        <span className="recurring-picker-name">{chore.name}</span>
                        <span className="recurring-picker-cadence">{chore.cadence ?? 'weekly'}</span>
                      </span>
                      {chore.description ? (
                        <span className="recurring-picker-description">{chore.description}</span>
                      ) : null}
                    </button>
                  ))
                ) : (
                  <div className="recurring-picker-empty">No recurring chores found</div>
                )}
              </div>
            </Tab>
            <Tab eventKey="new" title="Make this a new recurring chore">
              <Form.Group className="mb-3" controlId="new-recurring-name">
                <Form.Label>Chore</Form.Label>
                <Form.Control
                  value={recurringModal?.newName ?? ''}
                  onChange={(event) =>
                    setRecurringModal((current: any) =>
                      current ? { ...current, newName: event.target.value } : current,
                    )
                  }
                />
              </Form.Group>
              <Form.Group className="mb-3" controlId="new-recurring-description">
                <Form.Label>Description</Form.Label>
                <Form.Control
                  value={recurringModal?.newDescription ?? ''}
                  onChange={(event) =>
                    setRecurringModal((current: any) =>
                      current ? { ...current, newDescription: event.target.value } : current,
                    )
                  }
                />
              </Form.Group>
              <Form.Group className="mb-3" controlId="new-recurring-cadence">
                <Form.Label>Cadence</Form.Label>
                <Form.Select
                  value={recurringModal?.newCadence ?? 'weekly'}
                  onChange={(event) =>
                    setRecurringModal((current: any) =>
                      current ? { ...current, newCadence: event.target.value } : current,
                    )
                  }
                >
                  {CADENCES.map((cadence) => (
                    <option key={cadence.value} value={cadence.value}>
                      {cadence.label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
              <Button onClick={convertToNewRecurring} disabled={!recurringModal?.newName?.trim()}>
                Create recurring chore
              </Button>
            </Tab>
            <Tab eventKey="manual" title="Manual Override Chore">
              <Form.Group className="mb-3" controlId="manual-assignee">
                <Form.Label>Assigned to</Form.Label>
                <Form.Select
                  value={recurringModal?.manualAssigneeId ?? ''}
                  onChange={(event) =>
                    setRecurringModal((current: any) =>
                      current ? { ...current, manualAssigneeId: event.target.value } : current,
                    )
                  }
                >
                  <option value="">Unassigned</option>
                  {roommates.map((roommate: any) => (
                    <option key={roommate.id} value={roommate.id}>
                      {roommate.name}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
              <Form.Group className="mb-3" controlId="manual-price">
                <Form.Label>Price</Form.Label>
                <Form.Control
                  type="number"
                  min="0"
                  step="0.01"
                  value={recurringModal?.manualPayout ?? '0.00'}
                  onChange={(event) =>
                    setRecurringModal((current: any) =>
                      current ? { ...current, manualPayout: event.target.value } : current,
                    )
                  }
                />
              </Form.Group>
              <Button onClick={saveManualOverride} disabled={!recurringModal?.manualAssigneeId}>
                Save manual override
              </Button>
            </Tab>
          </Tabs>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" onClick={() => setRecurringModal(null)}>
            Cancel
          </Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
