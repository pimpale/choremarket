// All chore-market economics live here, on the client. The backend is a thin
// store of primitives (roommates, recurring chores, per-chore wtp/bid, instance
// status, the active mechanism); everything derived -- who does each chore, the
// transfers, whether it's worth doing, balances and settlements -- is recomputed
// here from those primitives. There are at most a few hundred chore instances in
// a year, so doing this live on every render is trivial.

export type Mechanism = 'agv' | 'vcg';

export interface Pref {
  wtp_cents: number;
  bid_cents: number;
}

export interface NullablePref {
  wtp_cents: number | null;
  bid_cents: number | null;
}

const DEFAULT_ONE_OFF_BID_CENTS = 100_000_000;

export interface Person {
  id: number;
  name: string;
  // Membership window (ISO dates). null = open-ended on that side.
  joinDate?: string | null;
  leaveDate?: string | null;
}

// A roommate participates in a chore week iff that week falls inside their
// membership window. Weeks are compared as ISO strings (which sort correctly).
export function membersForWeek(people: Person[], weekStart?: string): Person[] {
  if (!weekStart) return people;
  return people.filter(
    (p) =>
      (p.joinDate == null || p.joinDate <= weekStart) &&
      (p.leaveDate == null || weekStart <= p.leaveDate),
  );
}

export interface Ledger {
  assigneeId: number | null;
  surplusCents: number;
  worthDoing: boolean;
  // roommate_id -> cents; positive pays, negative receives.
  payments: Record<number, number>;
}

// Rounds rational transfers to whole cents while preserving an exact sum: round
// all but the last (by id order), then make the last absorb the remainder.
function balancedRound(values: Record<number, number>): Record<number, number> {
  const ids = Object.keys(values)
    .map(Number)
    .sort((a, b) => a - b);
  const out: Record<number, number> = {};
  let running = 0;
  for (let i = 0; i < ids.length - 1; i += 1) {
    const v = Math.round(values[ids[i]]);
    out[ids[i]] = v;
    running += v;
  }
  if (ids.length) out[ids[ids.length - 1]] = -running;
  return out;
}

function pickAssignee(people: Person[], prefs: Record<number, Pref>, forcedId: number | null): Person {
  const forced = forcedId != null ? people.find((p) => p.id === forcedId) : undefined;
  if (forced) return forced;
  return [...people].sort((a, b) => {
    const bid = (prefs[a.id]?.bid_cents ?? 0) - (prefs[b.id]?.bid_cents ?? 0);
    if (bid !== 0) return bid;
    const name = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    if (name !== 0) return name;
    return a.id - b.id;
  })[0];
}

// AGV (d'AGVA expected-externality) transfer. Budget-balanced: payments sum to 0.
// v_i = wtp_i (minus bid_i for the doer); net receipt t_i = (sum_v - n*v_i)/(n-1);
// payment_i = -t_i (positive pays, negative receives).
function agvPayments(people: Person[], prefs: Record<number, Pref>, assigneeId: number): Record<number, number> {
  const value = (p: Person) => (prefs[p.id]?.wtp_cents ?? 0) - (p.id === assigneeId ? prefs[p.id]?.bid_cents ?? 0 : 0);
  const n = people.length;
  const totalValue = people.reduce((s, p) => s + value(p), 0);
  const raw: Record<number, number> = {};
  for (const p of people) raw[p.id] = (n * value(p) - totalValue) / (n - 1);
  return balancedRound(raw);
}

// VCG (Clarke pivot). NOT budget-balanced -- the house absorbs the difference.
function vcgPayments(
  people: Person[],
  prefs: Record<number, Pref>,
  assigneeId: number,
  winningBid: number,
): Record<number, number> {
  const totalWtp = people.reduce((s, p) => s + (prefs[p.id]?.wtp_cents ?? 0), 0);
  const others = people.filter((p) => p.id !== assigneeId);
  const secondLowestBid = Math.min(...others.map((p) => prefs[p.id]?.bid_cents ?? 0));

  const payments: Record<number, number> = {};
  // Doer is paid the second-lowest bid (capped at the value they uniquely unlock).
  const withoutDoerWtp = totalWtp - (prefs[assigneeId]?.wtp_cents ?? 0);
  payments[assigneeId] = Math.max(0, withoutDoerWtp - secondLowestBid) - withoutDoerWtp;
  // Non-doers pay only the Clarke tax when pivotal to doing the chore at all.
  for (const p of others) {
    const withoutI = totalWtp - (prefs[p.id]?.wtp_cents ?? 0);
    payments[p.id] = Math.max(0, withoutI - winningBid) - (withoutI - winningBid);
  }
  return payments;
}

// Balanced transfer for a directly-entered one-off: the assignee receives the
// payout, everyone else splits the cost so payments sum to exactly zero.
export function flatPayout(assigneeId: number | null, roommateIds: number[], payoutCents: number): Record<number, number> {
  const payments: Record<number, number> = {};
  for (const id of roommateIds) payments[id] = 0;
  if (assigneeId == null) return payments;
  if (!(assigneeId in payments)) payments[assigneeId] = 0;
  const others = Object.keys(payments).map(Number).filter((id) => id !== assigneeId);
  if (!others.length || payoutCents === 0) return payments;
  const base = Math.trunc(payoutCents / others.length);
  const remainder = payoutCents - base * others.length;
  others.sort((a, b) => a - b).forEach((id, index) => {
    payments[id] = base + (index < remainder ? 1 : 0);
  });
  payments[assigneeId] = -others.reduce((s, id) => s + payments[id], 0);
  return payments;
}

// Assign a chore and compute its transfer under the active mechanism. Mirrors
// the efficiency rule: do it only when total WTP covers the lowest bid; the
// assignee is the lowest bidder (or a forced override, which forces it done).
export function computeLedger(
  people: Person[],
  prefs: Record<number, Pref>,
  mechanism: Mechanism,
  forcedAssigneeId: number | null = null,
  forceWorthDoing = true,
): Ledger {
  if (!people.length) {
    return { assigneeId: null, surplusCents: 0, worthDoing: true, payments: {} };
  }
  if (people.length === 1) {
    return { assigneeId: people[0].id, surplusCents: 0, worthDoing: true, payments: { [people[0].id]: 0 } };
  }

  const assignee = pickAssignee(people, prefs, forcedAssigneeId);
  const totalWtp = people.reduce((s, p) => s + (prefs[p.id]?.wtp_cents ?? 0), 0);
  const winningBid = prefs[assignee.id]?.bid_cents ?? 0;
  const surplus = totalWtp - winningBid;

  if (surplus < 0 && (forcedAssigneeId == null || !forceWorthDoing)) {
    return { assigneeId: null, surplusCents: surplus, worthDoing: false, payments: {} };
  }

  const payments =
    mechanism === 'vcg'
      ? vcgPayments(people, prefs, assignee.id, winningBid)
      : agvPayments(people, prefs, assignee.id);
  return { assigneeId: assignee.id, surplusCents: surplus, worthDoing: true, payments };
}

// ---- Per-instance ledger + display status ------------------------------------

export type DisplayStatus = 'pending' | 'done' | 'failed' | 'skipped';

export interface RawInstance {
  id: number;
  recurring_chore_id: number | null;
  assignee_id: number | null; // one-off / manual override; null = auto for recurring
  status: 'pending' | 'done' | 'failed';
  payout_cents: number; // one-off payout (ignored for recurring)
  manual_override?: boolean;
  week_start?: string; // used to filter participants by membership window
}

export interface InstanceLedger extends Ledger {
  displayStatus: DisplayStatus;
}

// prefsByChore: recurring_chore_id -> roommate_id -> Pref
export function ledgerForInstance(
  instance: RawInstance,
  people: Person[],
  prefsByChore: Record<number, Record<number, Pref>>,
  mechanism: Mechanism,
  prefsByInstance: Record<number, Record<number, NullablePref>> = {},
): InstanceLedger {
  // Only roommates whose membership covers this week take part in the chore.
  const members = membersForWeek(people, instance.week_start);
  let ledger: Ledger;
  if (instance.manual_override) {
    const payments = flatPayout(instance.assignee_id, members.map((p) => p.id), instance.payout_cents);
    ledger = {
      assigneeId: instance.assignee_id,
      surplusCents: instance.payout_cents,
      worthDoing: true,
      payments,
    };
  } else if (instance.recurring_chore_id == null) {
    const rawPrefs = prefsByInstance[instance.id] ?? {};
    const prefs = Object.fromEntries(
      members.map((person) => [
        person.id,
        {
          wtp_cents: rawPrefs[person.id]?.wtp_cents ?? 0,
          bid_cents: rawPrefs[person.id]?.bid_cents ?? DEFAULT_ONE_OFF_BID_CENTS,
        },
      ]),
    );
    ledger = computeLedger(members, prefs, mechanism, instance.assignee_id, false);
  } else {
    const prefs = prefsByChore[instance.recurring_chore_id] ?? {};
    ledger = computeLedger(members, prefs, mechanism, instance.assignee_id);
  }

  // 'skipped' is derived, never stored; a manual done/failed always wins.
  const displayStatus: DisplayStatus =
    instance.status === 'done' || instance.status === 'failed'
      ? instance.status
      : ledger.worthDoing
        ? 'pending'
        : 'skipped';
  return { ...ledger, displayStatus };
}

// ---- Balances ----------------------------------------------------------------

export interface Net {
  id: number;
  name: string;
  net_cents: number;
}

export interface Settlement {
  from: string;
  to: string;
  amount_cents: number;
}

export interface RecordedPayment {
  from_roommate_id: number;
  to_roommate_id: number;
  amount_cents: number;
}

export interface Balances {
  nets: Net[];
  settlements: Settlement[];
  houseCents: number;
}

// Only 'done' instances pay out. Nets are summed per roommate (membership is
// handled inside ledgerForInstance); recorded settle-up payments then move money
// between roommates without touching the house. The house is the counterparty
// that balances the chore ledger (0 under AGV, the deficit under VCG).
export function computeBalances(
  instances: RawInstance[],
  people: Person[],
  prefsByChore: Record<number, Record<number, Pref>>,
  mechanism: Mechanism,
  prefsByInstance: Record<number, Record<number, NullablePref>> = {},
  recordedPayments: RecordedPayment[] = [],
): Balances {
  const net: Record<number, number> = {};
  for (const p of people) net[p.id] = 0;
  let roommateTotal = 0;

  for (const instance of instances) {
    if (instance.status !== 'done') continue;
    const { payments } = ledgerForInstance(instance, people, prefsByChore, mechanism, prefsByInstance);
    for (const [id, amount] of Object.entries(payments)) {
      if (Number(id) in net) net[Number(id)] += amount;
      roommateTotal += amount;
    }
  }

  // A recorded payment of A -> B settles A's debt: A's net falls, B's rises.
  // It nets to zero across the two, so the house is unaffected.
  for (const pay of recordedPayments) {
    if (pay.from_roommate_id in net) net[pay.from_roommate_id] -= pay.amount_cents;
    if (pay.to_roommate_id in net) net[pay.to_roommate_id] += pay.amount_cents;
  }

  const nets: Net[] = people
    .map((p) => ({ id: p.id, name: p.name, net_cents: net[p.id] ?? 0 }))
    .sort((a, b) => a.name.localeCompare(b.name));

  const houseCents = -roommateTotal || 0;
  return { nets, settlements: settle(nets), houseCents };
}

// Greedy debtor/creditor matching, same as the old backend settle_balances.
function settle(nets: Net[]): Settlement[] {
  const debtors = nets.filter((n) => n.net_cents > 0).map((n) => ({ name: n.name, amount: n.net_cents }));
  const creditors = nets.filter((n) => n.net_cents < 0).map((n) => ({ name: n.name, amount: -n.net_cents }));
  const settlements: Settlement[] = [];
  let i = 0;
  let j = 0;
  while (i < debtors.length && j < creditors.length) {
    const amount = Math.min(debtors[i].amount, creditors[j].amount);
    if (amount) settlements.push({ from: debtors[i].name, to: creditors[j].name, amount_cents: amount });
    debtors[i].amount -= amount;
    creditors[j].amount -= amount;
    if (debtors[i].amount === 0) i += 1;
    if (creditors[j].amount === 0) j += 1;
  }
  return settlements;
}
