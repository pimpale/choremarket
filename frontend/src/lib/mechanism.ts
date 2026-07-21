// All chore-market economics live here, on the client. The backend is a thin
// store of primitives (roommates, recurring chores, per-chore wtp/bid, instance
// status, the active mechanism); everything derived -- who does each chore, the
// transfers, whether it's worth doing, balances and settlements -- is recomputed
// here from those primitives. There are at most a few hundred chore instances in
// a year, so doing this live on every render is trivial.
//
// Every mechanism here is exactly budget-balanced: each chore's transfers sum
// to zero, so no house account is ever needed. The performer receives the
// entered price in full, financed by an equal split among the *other*
// roommates -- the doer never pays a share of their own price (matching
// flatPayout one-offs). Internally this stays the laboratory's *symmetric*
// mechanism (everyone, doer included, pays a 1/n share): the system simply
// prices the chore at price * n/(n-1), so the doer's own 1/n share cancels
// against their pay and each other roommate pays price/(n-1). Every funding
// decision and fairness computation runs on that scaled price, keeping the
// lab's incentive audits applicable verbatim:
//   - 'first-best': the price is the doer's own bid, paid whenever total WTP
//     covers it. The efficiency benchmark -- not strategyproof.
//   - 'vickrey-majority': the price is the second-lowest bid (a Vickrey
//     procurement, so bidding your true cost is dominant); the chore happens
//     only if a strict majority think it worth the per-head share.
//   - 'vickrey-faltings': the same Vickrey price, but the funding decision is
//     delegated to a randomly drawn "jury" of everyone-but-one, and the drawn
//     roommate's exclusion is compensated by FaltingsFair side-payments that
//     sum to zero. The draw is realized deterministically from the instance id.
//     Because money only moves when a chore actually happens, the fairness
//     side-payments are scaled by n/k (k = number of funding juries) so their
//     expectation over draws matches the always-paid mechanism — an exhaustive
//     grid audit shows this settlement is exactly as incentive-compatible as
//     paying them unconditionally, while dropping them unscaled is not.

export type Mechanism = 'first-best' | 'vickrey-majority' | 'vickrey-faltings';

export interface Pref {
  wtp_cents: number;
  bid_cents: number;
}

export interface NullablePref {
  wtp_cents: number | null;
  bid_cents: number | null;
}

// One entry in a recurring chore's append-only wtp/bid edit log: the value set
// by an edit made at `created_at` (an ISO datetime).
export interface TimedPref extends NullablePref {
  created_at: string;
}

// recurring_chore_id -> roommate_id -> edit log (sorted by created_at asc).
export type PrefHistoryByChore = Record<number, Record<number, TimedPref[]>>;

// An unset bid (recurring or one-off) is treated as a very large ask, so a
// roommate who never bid is never the cheapest and never auto-assigned.
export const DEFAULT_UNSET_BID_CENTS = 100_000_000;

// Add `days` to an ISO date, in UTC, so no timezone can shift the day across a
// boundary. Used to turn a week's Sunday start into its Saturday end.
function addDaysIso(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

// The wtp/bid in force for the week starting `weekStart` from a single
// (roommate, chore) edit log: the most recent edit that was made on or before
// the end (Saturday) of that week. Unset -> null/null.
export function effectivePref(entries: TimedPref[] | undefined, weekStart?: string): NullablePref {
  if (!entries || !entries.length) return { wtp_cents: null, bid_cents: null };
  const weekEnd = weekStart ? addDaysIso(weekStart, 6) : '9999-12-31';
  let chosen: TimedPref | undefined;
  for (const entry of [...entries].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    if (entry.created_at.slice(0, 10) <= weekEnd) chosen = entry;
    else break;
  }
  return chosen
    ? { wtp_cents: chosen.wtp_cents, bid_cents: chosen.bid_cents }
    : { wtp_cents: null, bid_cents: null };
}

// Resolve a whole chore's edit log into the concrete {roommate -> Pref} in force
// for a week, filling unset values with the defaults (no WTP, a very large bid).
function resolveChorePrefs(
  history: Record<number, TimedPref[]>,
  members: Person[],
  weekStart?: string,
): Record<number, Pref> {
  const prefs: Record<number, Pref> = {};
  for (const person of members) {
    const eff = effectivePref(history[person.id], weekStart);
    prefs[person.id] = {
      wtp_cents: eff.wtp_cents ?? 0,
      bid_cents: eff.bid_cents ?? DEFAULT_UNSET_BID_CENTS,
    };
  }
  return prefs;
}

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

// The per-mechanism numbers the ledger's Mechanism columns display.
export interface MechanismDetail {
  // What the doer is paid: their own bid under 'first-best', the Vickrey
  // (second-lowest) bid otherwise.
  priceCents: number;
  // Whose bid set the price (null under 'first-best', where it's the doer's own).
  priceSetterId: number | null;
  // The equal per-head share financing that price: the 1/n share of the
  // n/(n-1)-scaled system price, i.e. price/(n-1), which is both what each
  // non-doer pays and the WTP threshold the funding decisions test against
  // (display-rounded).
  shareCents: number;
  // 'vickrey-majority': how many members' WTP covers the share, and the
  // strict-majority threshold that decides funding.
  supporters?: number;
  required?: number;
  // 'vickrey-faltings': the roommate excluded by the realized draw, whether the
  // remaining jury funded the chore, how many of the n juries fund, and each
  // member's zero-sum fairness side-payment as it settles here — already
  // scaled by n / fundingJuries (positive receives).
  excludedId?: number;
  juryFunds?: boolean;
  fundingJuries?: number;
  fairAdjustmentCents?: Record<number, number>;
}

export interface Ledger {
  assigneeId: number | null;
  surplusCents: number;
  worthDoing: boolean;
  // Mechanism-specific explanation when the chore is skipped.
  skipReason?: string;
  // roommate_id -> cents; positive pays, negative receives.
  payments: Record<number, number>;
  // 'vickrey-faltings' only: `payments` split into the money that actually
  // finances the chore and the zero-sum fairness incentives from the jury
  // draw. Each part is exactly balanced and payments is their sum.
  chorePaymentsCents?: Record<number, number>;
  incentivePaymentsCents?: Record<number, number>;
  detail?: MechanismDetail;
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
    const v = Math.round(values[ids[i]]) || 0; // normalize -0
    out[ids[i]] = v;
    running += v;
  }
  if (ids.length) out[ids[ids.length - 1]] = -running || 0;
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

// The Vickrey procurement price for a given performer: the lowest bid among
// everyone else (the second-lowest bid when the performer is the natural
// lowest bidder).
function vickreyPrice(people: Person[], prefs: Record<number, Pref>, assigneeId: number): number {
  const others = people.filter((p) => p.id !== assigneeId);
  return Math.min(...others.map((p) => prefs[p.id]?.bid_cents ?? 0));
}

function priceSetter(people: Person[], prefs: Record<number, Pref>, assigneeId: number, price: number): number | null {
  const setter = people
    .filter((p) => p.id !== assigneeId && (prefs[p.id]?.bid_cents ?? 0) === price)
    .sort((a, b) => a.id - b.id)[0];
  return setter?.id ?? null;
}

// The other roommates split the price equally; the doer receives it in full
// and never finances their own pay. Sums to zero before rounding;
// balancedRound keeps it exact.
function equalSplitPayments(people: Person[], assigneeId: number, price: number): Record<number, number> {
  const share = price / (people.length - 1);
  const raw: Record<number, number> = {};
  for (const p of people) raw[p.id] = p.id === assigneeId ? -price : share;
  return balancedRound(raw);
}

// FaltingsFair side-payments and the realized jury draw, on the symmetric
// 1/n-share economics of `price` (callers pass the n/(n-1)-scaled system
// price, mirroring the laboratory exactly). Each member's fairness transfer
// is their expected VCG pivot charge across the draws that include them,
// minus 1/n of the charges levied when they are the excluded one -- the
// rebate depends only on the *others'* reports and the charges are standard
// pivot terms, so truthful reporting stays optimal in expectation.
// The transfers sum to zero, so exact budget balance survives any realized
// draw. `fairTransfers` comes back scaled by n/k (k = funding juries): the app
// only settles funded chores, and scaling keeps each member's expected
// fairness payment over the draws equal to the unconditional mechanism's.
function faltingsDraw(
  people: Person[],
  prefs: Record<number, Pref>,
  price: number,
  drawKey: number,
): {
  excludedId: number;
  juryFunds: boolean;
  fundingJuries: number;
  fairTransfers: Record<number, number>;
} {
  const n = people.length;
  const share = price / n;
  const ids = people.map((p) => p.id).sort((a, b) => a - b);
  const netValue: Record<number, number> = {};
  for (const p of people) netValue[p.id] = (prefs[p.id]?.wtp_cents ?? 0) - share;

  // Pivot charges in the reduced economy without `excluded`: agent i is charged
  // the welfare the rest would gain by overruling the jury's funding choice.
  const chargesWithout = (excluded: number): Record<number, number> => {
    const agents = ids.filter((id) => id !== excluded);
    const funds = agents.reduce((s, id) => s + netValue[id], 0) >= -1e-9;
    const charges: Record<number, number> = {};
    for (const i of agents) {
      const othersSum = agents.filter((j) => j !== i).reduce((s, j) => s + netValue[j], 0);
      charges[i] = Math.max(0, othersSum) - (funds ? othersSum : 0);
    }
    return charges;
  };

  const reducedCharges = new Map<number, Record<number, number>>(
    ids.map((excluded) => [excluded, chargesWithout(excluded)]),
  );
  const fairTransfers: Record<number, number> = {};
  for (const i of ids) {
    let expectedOwnCharge = 0;
    for (const excluded of ids) {
      if (excluded !== i) expectedOwnCharge += reducedCharges.get(excluded)![i] ?? 0;
    }
    expectedOwnCharge /= n;
    const rebate = Object.values(reducedCharges.get(i)!).reduce((s, c) => s + c, 0) / n;
    fairTransfers[i] = rebate - expectedOwnCharge; // positive receives
  }

  const fundingJuries = ids.filter((excluded) =>
    ids.filter((id) => id !== excluded).reduce((s, id) => s + netValue[id], 0) >= -1e-9,
  ).length;
  if (fundingJuries) {
    for (const i of ids) fairTransfers[i] *= n / fundingJuries;
  }

  const excludedId = ids[((drawKey % n) + n) % n];
  const jury = ids.filter((id) => id !== excludedId);
  const juryFunds = jury.reduce((s, id) => s + netValue[id], 0) >= -1e-9;
  return { excludedId, juryFunds, fundingJuries, fairTransfers };
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

// Assign a chore and compute its transfer under the active mechanism. The
// assignee is the lowest bidder (or a forced override, which forces it done);
// whether the chore happens at all is mechanism-specific: total WTP must cover
// the bid ('first-best'), a strict majority must accept the per-head share
// ('vickrey-majority'), or the drawn jury must value funding nonnegatively
// ('vickrey-faltings'). `drawKey` seeds the faltings draw (the instance id).
export function computeLedger(
  people: Person[],
  prefs: Record<number, Pref>,
  mechanism: Mechanism,
  forcedAssigneeId: number | null = null,
  forceWorthDoing = true,
  drawKey = 0,
): Ledger {
  if (!people.length) {
    return { assigneeId: null, surplusCents: 0, worthDoing: true, payments: {} };
  }
  if (people.length === 1) {
    return { assigneeId: people[0].id, surplusCents: 0, worthDoing: true, payments: { [people[0].id]: 0 } };
  }

  const n = people.length;
  const assignee = pickAssignee(people, prefs, forcedAssigneeId);
  const totalWtp = people.reduce((s, p) => s + (prefs[p.id]?.wtp_cents ?? 0), 0);
  const winningBid = prefs[assignee.id]?.bid_cents ?? 0;
  const surplus = totalWtp - winningBid;
  const forced = forcedAssigneeId != null && forceWorthDoing;

  const price = mechanism === 'first-best' ? winningBid : vickreyPrice(people, prefs, assignee.id);
  // The doer nets `price` in full. To stay exactly the lab's symmetric
  // mechanism (everyone, doer included, pays a 1/n share), the system prices
  // the chore at price * n/(n-1): the doer's 1/n share of the scaled price
  // cancels against their pay, leaving each other roommate paying
  // price/(n-1). All funding decisions and fairness math use the scaled
  // price, so the lab's incentive audits apply verbatim.
  const systemPrice = (price * n) / (n - 1);
  const share = systemPrice / n;
  const detail: MechanismDetail = {
    priceCents: price,
    priceSetterId: mechanism === 'first-best' ? null : priceSetter(people, prefs, assignee.id, price),
    shareCents: Math.round(share),
  };

  let funds: boolean;
  let skipReason: string | undefined;
  let fairTransfers: Record<number, number> | undefined;

  if (mechanism === 'vickrey-majority') {
    detail.supporters = people.filter((p) => (prefs[p.id]?.wtp_cents ?? 0) + 1e-9 >= share).length;
    detail.required = Math.ceil((n + 1) / 2);
    funds = detail.supporters >= detail.required;
    if (!funds) {
      skipReason = `only ${detail.supporters} of ${n} accept the per-head share (need ${detail.required})`;
    }
  } else if (mechanism === 'vickrey-faltings') {
    const draw = faltingsDraw(people, prefs, systemPrice, drawKey);
    detail.excludedId = draw.excludedId;
    detail.juryFunds = draw.juryFunds;
    detail.fundingJuries = draw.fundingJuries;
    detail.fairAdjustmentCents = draw.fairTransfers;
    fairTransfers = draw.fairTransfers;
    funds = draw.juryFunds;
    if (!funds) skipReason = 'the drawn jury values the chore below its price';
  } else {
    funds = surplus >= 0;
    if (!funds) skipReason = 'total WTP is below the lowest bid';
  }

  if (!funds && !forced) {
    return { assigneeId: null, surplusCents: surplus, worthDoing: false, skipReason, payments: {}, detail };
  }

  // Settle the chore financing and the fairness incentives as two separately
  // balanced parts so the UI can show them apart; `payments` is their sum.
  const chorePayments = equalSplitPayments(people, assignee.id, price);
  if (!fairTransfers) {
    return { assigneeId: assignee.id, surplusCents: surplus, worthDoing: true, payments: chorePayments, detail };
  }
  const rawIncentives: Record<number, number> = {};
  for (const p of people) rawIncentives[p.id] = -(fairTransfers[p.id] ?? 0);
  const incentivePayments = balancedRound(rawIncentives);
  const payments: Record<number, number> = {};
  for (const p of people) payments[p.id] = (chorePayments[p.id] ?? 0) + (incentivePayments[p.id] ?? 0);
  return {
    assigneeId: assignee.id,
    surplusCents: surplus,
    worthDoing: true,
    payments,
    chorePaymentsCents: chorePayments,
    incentivePaymentsCents: incentivePayments,
    detail,
  };
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

// prefsByChore: recurring_chore_id -> roommate_id -> Pref (a single value per
// chore). prefsHistoryByChore, when given, supersedes it for recurring chores:
// each instance resolves the wtp/bid that was effective for *its own week*, so
// past weeks reflect the value in force then and edits are future-only.
export function ledgerForInstance(
  instance: RawInstance,
  people: Person[],
  prefsByChore: Record<number, Record<number, Pref>>,
  mechanism: Mechanism,
  prefsByInstance: Record<number, Record<number, NullablePref>> = {},
  prefsHistoryByChore?: PrefHistoryByChore,
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
          bid_cents: rawPrefs[person.id]?.bid_cents ?? DEFAULT_UNSET_BID_CENTS,
        },
      ]),
    );
    ledger = computeLedger(members, prefs, mechanism, instance.assignee_id, false, instance.id);
  } else {
    const prefs = prefsHistoryByChore
      ? resolveChorePrefs(prefsHistoryByChore[instance.recurring_chore_id] ?? {}, members, instance.week_start)
      : prefsByChore[instance.recurring_chore_id] ?? {};
    ledger = computeLedger(members, prefs, mechanism, instance.assignee_id, true, instance.id);
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
}

// Only 'done' instances pay out. Nets are summed per roommate (membership is
// handled inside ledgerForInstance); recorded settle-up payments then move money
// between roommates. Every mechanism is exactly budget-balanced, so money only
// ever moves between roommates -- there is no house account.
export function computeBalances(
  instances: RawInstance[],
  people: Person[],
  prefsByChore: Record<number, Record<number, Pref>>,
  mechanism: Mechanism,
  prefsByInstance: Record<number, Record<number, NullablePref>> = {},
  recordedPayments: RecordedPayment[] = [],
  prefsHistoryByChore?: PrefHistoryByChore,
): Balances {
  const net: Record<number, number> = {};
  for (const p of people) net[p.id] = 0;

  for (const instance of instances) {
    if (instance.status !== 'done') continue;
    const { payments } = ledgerForInstance(
      instance,
      people,
      prefsByChore,
      mechanism,
      prefsByInstance,
      prefsHistoryByChore,
    );
    for (const [id, amount] of Object.entries(payments)) {
      if (Number(id) in net) net[Number(id)] += amount;
    }
  }

  // A recorded payment of A -> B settles A's debt: A's net falls, B's rises.
  // It nets to zero across the two.
  for (const pay of recordedPayments) {
    if (pay.from_roommate_id in net) net[pay.from_roommate_id] -= pay.amount_cents;
    if (pay.to_roommate_id in net) net[pay.to_roommate_id] += pay.amount_cents;
  }

  const nets: Net[] = people
    .map((p) => ({ id: p.id, name: p.name, net_cents: net[p.id] ?? 0 }))
    .sort((a, b) => a.name.localeCompare(b.name));

  return { nets, settlements: settle(nets) };
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
