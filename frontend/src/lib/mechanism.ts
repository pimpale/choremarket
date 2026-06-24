// All chore-market economics live here, on the client. The backend is a thin
// store of primitives (roommates, recurring chores, per-chore wtp/bid, instance
// status, the active mechanism); everything derived -- who does each chore, the
// transfers, whether it's worth doing, balances and settlements -- is recomputed
// here from those primitives. There are at most a few hundred chore instances in
// a year, so doing this live on every render is trivial.

export type Mechanism = 'agv' | 'vcg' | 'bailey-cavallo';

// Financing is orthogonal to the mechanism. 'none' lets the house absorb any VCG
// deficit (the textbook behaviour); 'ema' amortizes that deficit with a flat
// weekly levy so the house never pays out of pocket (see computeFinancing).
export type Financing = 'none' | 'ema';

// The levy each settled week is the EMA of past weekly deficits, marked up
// slightly so we err toward a small (burnable) surplus rather than a deficit that
// would strand unpaid doers at the end of a lease.
const FINANCING_EMA_ALPHA = 0.5;
const FINANCING_MARKUP = 1.025;

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

// Total VCG payment collected by the house for a set of people under the natural
// (lowest-bidder) allocation: positive = net collected from roommates, negative =
// net paid out. 0 when there's nobody to do the chore or it isn't worth doing.
// This is the "VCG revenue" the Cavallo rebate redistributes.
function vcgRevenue(people: Person[], prefs: Record<number, Pref>): number {
  if (people.length < 2) return 0;
  const assignee = pickAssignee(people, prefs, null);
  const winningBid = prefs[assignee.id]?.bid_cents ?? 0;
  const totalWtp = people.reduce((s, p) => s + (prefs[p.id]?.wtp_cents ?? 0), 0);
  if (totalWtp - winningBid < 0) return 0; // not worth doing -> no transfers
  const payments = vcgPayments(people, prefs, assignee.id, winningBid);
  return Object.values(payments).reduce((s, a) => s + a, 0);
}

// Bailey-Cavallo, symmetric (deficit-sharing) variant. Each roommate's VCG payment
// is adjusted by h_i = R_{-i}/n, where R_{-i} is the VCG revenue the *other*
// roommates would generate without them. When R_{-i} > 0 that's the textbook
// Cavallo rebate (hand a surplus back); when R_{-i} < 0 it's the mirror image -- a
// charge that shares the deficit the rest of the house would run. Either way h_i
// depends only on the *other* roommates' reports, so -- exactly like the rebate --
// it stays a Groves mechanism and remains strategyproof. The sign is irrelevant to
// incentives. In this single-chore setting VCG runs a deficit, so this is mostly
// the charge side: it pulls the house deficit back toward zero by levying roommates.
//
// Two textbook Cavallo guarantees do NOT survive the deficit direction, by design:
//   - It is no longer individually rational: a roommate can be charged more than the
//     chore is worth to them (the rebate side only ever pays roommates).
//   - It does not reach exact budget balance. Cavallo's feasibility theorem
//     (rebates <= surplus) is one-directional, so the charges need not sum to the
//     deficit -- the house is left with a smaller residual deficit OR a small surplus.
// Crucially we must NOT cap/renormalise to force exact balance: the cap would depend
// on the full-economy revenue (which includes a roommate's own report), so a binding
// cap reintroduces own-report dependence and breaks strategyproofness. We take the
// signed h_i straight and let the house carry whatever residual is left.
function baileyCavalloPayments(
  people: Person[],
  prefs: Record<number, Pref>,
  assigneeId: number,
  winningBid: number,
): Record<number, number> {
  const base = vcgPayments(people, prefs, assigneeId, winningBid);
  const n = people.length;
  const out: Record<number, number> = {};
  for (const p of people) {
    const others = people.filter((q) => q.id !== p.id);
    out[p.id] = Math.round(base[p.id] - vcgRevenue(others, prefs) / n);
  }
  return out;
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
      : mechanism === 'bailey-cavallo'
        ? baileyCavalloPayments(people, prefs, assignee.id, winningBid)
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
  // 'ema' financing only: the levy each member owes for the next settled week (the
  // marked-up EMA of past weekly deficits). 0 when financing is off.
  weeklyRateCents: number;
}

// What 'ema' financing does to one week.
export interface WeekFinancing {
  deficitCents: number; // this week's aggregate house deficit (across all chores)
  emaRateCents: number; // smoothed deficit from prior weeks -> this week's basis
  levyCents: number; // marked-up amount the household funds this week (0 the first week)
  perMemberCents: number; // levy split equally across this week's members
  memberCount: number;
  // True once the week has any settled (done) chore. The levy only depends on
  // *prior* weeks, so an unsettled week (this week, next week) still has a known,
  // projected levy -- but only settled weeks feed the EMA and count in balances.
  settled: boolean;
}

export interface FinancingResult {
  // Keyed by week_start; includes unsettled weeks (with a projected levy).
  schedule: Map<string, WeekFinancing>;
  // The marked-up trailing EMA: what the next settled week would levy.
  nextRateCents: number;
}

// Walk every week oldest-first. Each week levies the marked-up EMA of *prior*
// settled weeks' deficits (so the first settled week levies nothing); only a
// settled week then folds its own deficit into the EMA. The levy is just
// roommate->pot flow, so in computeBalances it slots into the existing
// nets/settle() machinery: levy-payers become debtors that settle() pairs against
// the doer-creditors, paying them out over subsequent weeks. The EMA tracks the
// raw deficit; the markup biases toward a small surplus.
export function computeFinancing(
  instances: RawInstance[],
  people: Person[],
  prefsByChore: Record<number, Record<number, Pref>>,
  mechanism: Mechanism,
  prefsByInstance: Record<number, Record<number, NullablePref>> = {},
): FinancingResult {
  // Every week that has chores, plus the realized deficit from its settled ones.
  const weeks = new Set<string>();
  const deficitByWeek = new Map<string, number>();
  for (const instance of instances) {
    const week = instance.week_start ?? '';
    weeks.add(week);
    if (instance.status !== 'done') continue;
    const { payments } = ledgerForInstance(instance, people, prefsByChore, mechanism, prefsByInstance);
    const roommateFlow = Object.values(payments).reduce((s, a) => s + a, 0);
    deficitByWeek.set(week, (deficitByWeek.get(week) ?? 0) - roommateFlow);
  }

  const schedule = new Map<string, WeekFinancing>();
  let ema: number | null = null;
  for (const week of [...weeks].sort()) {
    const settled = deficitByWeek.has(week);
    const levy = ema == null ? 0 : Math.max(0, Math.round(ema * FINANCING_MARKUP));
    const members = membersForWeek(people, week);
    schedule.set(week, {
      deficitCents: deficitByWeek.get(week) ?? 0,
      emaRateCents: ema == null ? 0 : Math.round(ema),
      levyCents: levy,
      perMemberCents: members.length ? Math.round(levy / members.length) : 0,
      memberCount: members.length,
      settled,
    });
    if (settled) {
      const deficit = deficitByWeek.get(week) ?? 0;
      ema = ema == null ? deficit : (1 - FINANCING_EMA_ALPHA) * ema + FINANCING_EMA_ALPHA * deficit;
    }
  }
  const nextRateCents = ema == null ? 0 : Math.max(0, Math.round(ema * FINANCING_MARKUP));
  return { schedule, nextRateCents };
}

// Actual net cash that moves for each roommate in a week.
export interface WeekCashflow {
  byRoommate: Map<number, number>; // roommate id -> net cash (+ received, - paid)
  // The house's *notional* running balance after the week: cumulative entitlements
  // owed minus the full marked-up levy assessed. Positive = still owed (deficit);
  // negative = surplus. Uncapped -- the markup keeps accruing surplus even when no
  // cash changes hands, so this is a buffer on the books to be burned later.
  houseDeficitCents: number;
  settled: boolean; // false = projected (this week's chores aren't done yet)
}

// Simulate the financed cash flow week by week.
//
// Entitlements are *allocation*-based: a chore credits its doer the moment it is
// allocated (worth doing + assigned), so this/next week's pending chores count;
// only a chore marked failed (or not worth doing) is dropped. The doer is credited
// when allocated but only *paid* as the house pays its IOUs down (oldest first).
//
// Two ledgers run in parallel:
//   - Cash (what actually moves): the house collects only what it needs to pay its
//     outstanding IOUs -- never more -- so it never over-collects into a pile. Each
//     roommate's net = the cash levy they pay minus any payout they receive.
//   - Notional (the books): the full marked-up levy is assessed every week even
//     when no cash is collected, so surplus accrues as a buffer (houseDeficitCents).
export function computeFinancingCashflow(
  instances: RawInstance[],
  people: Person[],
  prefsByChore: Record<number, Record<number, Pref>>,
  mechanism: Mechanism,
  prefsByInstance: Record<number, Record<number, NullablePref>> = {},
): Map<string, WeekCashflow> {
  const { schedule } = computeFinancing(instances, people, prefsByChore, mechanism, prefsByInstance);

  // Allocation-based receipts per (week, roommate): positive = a doer owed money,
  // negative = a pivotal Clarke payer who owes the house now. Failed / not-worth-
  // doing chores are skipped (no allocation, so no entitlement).
  const receiptsByWeek = new Map<string, Map<number, number>>();
  for (const instance of instances) {
    const ledger = ledgerForInstance(instance, people, prefsByChore, mechanism, prefsByInstance);
    if (ledger.displayStatus === 'failed' || ledger.displayStatus === 'skipped') continue;
    const week = instance.week_start ?? '';
    const into = receiptsByWeek.get(week) ?? new Map<number, number>();
    for (const [id, amount] of Object.entries(ledger.payments)) {
      const rid = Number(id);
      into.set(rid, (into.get(rid) ?? 0) - amount); // -payment = receipt
    }
    receiptsByWeek.set(week, into);
  }

  const queue: { id: number; amount: number }[] = []; // outstanding cash IOUs, oldest first
  let cash = 0; // house cash reserve carried between weeks
  let notional = 0; // notional house balance (+ owe / deficit, - surplus); uncapped
  const out = new Map<string, WeekCashflow>();

  for (const week of [...schedule.keys()].sort()) {
    const wk = schedule.get(week)!;
    const members = membersForWeek(people, week);
    const net = new Map<number, number>(members.map((m) => [m.id, 0]));
    const credit = (id: number, delta: number) => net.set(id, (net.get(id) ?? 0) + delta);

    // 1. Book this week's allocated entitlements. Doers join the IOU queue; pivotal
    //    Clarke payers settle in cash immediately. Track the week's net deficit.
    let weekDeficit = 0;
    for (const [id, receipt] of receiptsByWeek.get(week) ?? []) {
      weekDeficit += receipt;
      if (receipt > 0) queue.push({ id, amount: receipt });
      else if (receipt < 0) {
        credit(id, receipt);
        cash += -receipt;
      }
    }

    // 2. Notional: assess the full marked-up levy whether or not cash is collected.
    notional += weekDeficit - wk.levyCents;

    // 3. Cash: collect only enough to cover the outstanding IOUs, never more, and
    //    never faster than the marked-up levy. Split equally across members.
    const outstanding = queue.reduce((s, iou) => s + iou.amount, 0);
    const cashLevy = Math.min(wk.levyCents, Math.max(0, outstanding - cash));
    const cashPerMember = members.length ? Math.round(cashLevy / members.length) : 0;
    for (const m of members) {
      credit(m.id, -cashPerMember);
      cash += cashPerMember;
    }

    // 4. Pay the house's oldest IOUs from whatever cash it now holds.
    while (cash > 0 && queue.length) {
      const iou = queue[0];
      const pay = Math.min(cash, iou.amount);
      iou.amount -= pay;
      cash -= pay;
      credit(iou.id, pay);
      if (iou.amount <= 0) queue.shift();
    }

    out.set(week, { byRoommate: net, houseDeficitCents: notional, settled: wk.settled });
  }
  return out;
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
  financing: Financing = 'none',
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

  // 'ema' financing: levy the marked-up EMA rate on each settled week's members.
  // This is a roommate->pot flow, so it lifts roommateTotal and shrinks the house
  // residual (the financing float) toward a small surplus over time.
  let weeklyRateCents = 0;
  if (financing === 'ema') {
    const { schedule, nextRateCents } = computeFinancing(instances, people, prefsByChore, mechanism, prefsByInstance);
    for (const [week, f] of schedule) {
      if (!f.settled) continue; // an unsettled week shows a projected levy but isn't collected yet
      for (const member of membersForWeek(people, week)) {
        if (member.id in net) net[member.id] += f.perMemberCents;
        roommateTotal += f.perMemberCents;
      }
    }
    weeklyRateCents = nextRateCents;
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
  return { nets, settlements: settle(nets), houseCents, weeklyRateCents };
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
