import { describe, expect, it } from 'vitest';

import {
  computeBalances,
  computeFinancing,
  computeFinancingCashflow,
  computeLedger,
  flatPayout,
  ledgerForInstance,
  membersForWeek,
  type Pref,
  type Person,
  type RawInstance,
} from './mechanism';

const people: Person[] = [
  { id: 1, name: 'Alex' },
  { id: 2, name: 'Blair' },
  { id: 3, name: 'Casey' },
];
const P = (wtp_cents: number, bid_cents: number): Pref => ({ wtp_cents, bid_cents });

describe('assignment', () => {
  it('assigns the lowest bidder', () => {
    const led = computeLedger(people, { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) }, 'agv');
    expect(led.assigneeId).toBe(2);
  });

  it('breaks bid ties by name then id', () => {
    const two = [
      { id: 1, name: 'Blair' },
      { id: 2, name: 'Alex' },
    ];
    const led = computeLedger(two, { 1: P(1000, 500), 2: P(1000, 500) }, 'agv');
    expect(led.assigneeId).toBe(2); // "Alex" < "Blair"
  });
});

describe('AGV (d\'AGVA expected externality)', () => {
  it('matches t_i = (sum_v - n*v_i)/(n-1) and is budget-balanced', () => {
    // v = {Alex:1500, Blair:1200-700=500, Casey:1800}, sum_v=3800, n=3.
    const led = computeLedger(people, { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) }, 'agv');
    expect(led.payments).toEqual({ 1: 350, 2: -1150, 3: 800 });
    expect(led.payments[1] + led.payments[2] + led.payments[3]).toBe(0);
  });
});

describe('VCG (Clarke)', () => {
  it('pays the doer the second-lowest bid; non-doers pay 0; house eats the rest', () => {
    const led = computeLedger(people, { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) }, 'vcg');
    expect(led.assigneeId).toBe(2);
    expect(led.payments).toEqual({ 1: 0, 2: -900, 3: 0 });
    expect(led.payments[1] + led.payments[2] + led.payments[3]).toBe(-900); // house deficit 900
  });

  it('charges a pivotal non-doer the shortfall', () => {
    // Without Blair's WTP (700) the total (400) can't cover the lowest bid (500).
    const led = computeLedger(people, { 1: P(100, 500), 2: P(700, 900), 3: P(300, 1000) }, 'vcg');
    expect(led.assigneeId).toBe(1);
    expect(led.payments).toEqual({ 1: -900, 2: 100, 3: 0 });
  });
});

describe('not worth doing', () => {
  it.each(['agv', 'vcg'] as const)('skips when total WTP is below the lowest bid (%s)', (mechanism) => {
    const led = computeLedger(people, { 1: P(100, 900), 2: P(200, 700), 3: P(150, 1100) }, mechanism);
    expect(led.worthDoing).toBe(false);
    expect(led.assigneeId).toBeNull();
    expect(led.payments).toEqual({});
    expect(led.surplusCents).toBe(450 - 700);
  });

  it('forced assignee overrides "not worth doing"', () => {
    const led = computeLedger([people[0], people[1]], { 1: P(100, 900), 2: P(50, 700) }, 'vcg', 1);
    expect(led.worthDoing).toBe(true);
    expect(led.assigneeId).toBe(1);
  });
});

describe('one-offs', () => {
  it('splits a flat payout to net zero', () => {
    expect(flatPayout(1, [1, 2, 3, 4], 900)).toEqual({ 1: -900, 2: 300, 3: 300, 4: 300 });
  });

  it('derives a one-off ledger from per-instance prefs', () => {
    const instance: RawInstance = { id: 9, recurring_chore_id: null, assignee_id: 2, status: 'pending', payout_cents: 1200 };
    const led = ledgerForInstance(instance, people, {}, 'agv', {
      9: {
        1: P(1500, 900),
        2: P(1200, 700),
        3: P(1800, 1100),
      },
    });
    expect(led.assigneeId).toBe(2);
    expect(led.payments[2]).toBe(-1150);
    expect(Object.values(led.payments).reduce((s, a) => s + a, 0)).toBe(0);
  });

  it('defaults unset one-off prefs to no WTP and a very large bid', () => {
    const instance: RawInstance = { id: 10, recurring_chore_id: null, assignee_id: null, status: 'pending', payout_cents: 0 };
    const led = ledgerForInstance(instance, people, {}, 'vcg', {
      10: {
        1: { wtp_cents: null, bid_cents: null },
      },
    });
    expect(led.displayStatus).toBe('skipped');
    expect(led.surplusCents).toBe(-100_000_000);
  });

  it('does not force a one-off when selected assignee bid exceeds total WTP', () => {
    const instance: RawInstance = { id: 11, recurring_chore_id: null, assignee_id: 2, status: 'pending', payout_cents: 0 };
    const led = ledgerForInstance(instance, people, {}, 'agv', {
      11: {
        1: P(0, 100_000_000),
        2: P(0, 100_000_000),
        3: P(0, 100_000_000),
      },
    });
    expect(led.assigneeId).toBeNull();
    expect(led.displayStatus).toBe('skipped');
    expect(led.payments).toEqual({});
  });

  it('manual override one-offs use direct payout instead of WTP/Bid', () => {
    const instance: RawInstance = {
      id: 12,
      recurring_chore_id: null,
      assignee_id: 1,
      status: 'pending',
      payout_cents: 900,
      manual_override: true,
    };
    const led = ledgerForInstance(instance, people, {}, 'agv');
    expect(led.assigneeId).toBe(1);
    expect(led.displayStatus).toBe('pending');
    expect(led.payments).toEqual({ 1: -900, 2: 450, 3: 450 });
  });
});

describe('display status', () => {
  it('derives skipped, but never overrides a manual done/failed', () => {
    const prefsByChore = { 5: { 1: P(100, 900), 2: P(200, 700), 3: P(150, 1100) } };
    const base = { id: 1, recurring_chore_id: 5, assignee_id: null, payout_cents: 0 } as const;
    expect(ledgerForInstance({ ...base, status: 'pending' }, people, prefsByChore, 'agv').displayStatus).toBe('skipped');
    expect(ledgerForInstance({ ...base, status: 'done' }, people, prefsByChore, 'agv').displayStatus).toBe('done');
  });
});

describe('balances', () => {
  const prefsByChore = { 5: { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) } };
  const doneRecurring: RawInstance = { id: 1, recurring_chore_id: 5, assignee_id: null, status: 'done', payout_cents: 0 };
  const pendingRecurring: RawInstance = { ...doneRecurring, id: 2, status: 'pending' };

  it('AGV nets to zero with a flat house and only counts done', () => {
    const b = computeBalances([doneRecurring, pendingRecurring], people, prefsByChore, 'agv');
    expect(b.houseCents).toBe(0);
    expect(b.nets.reduce((s, n) => s + n.net_cents, 0)).toBe(0);
  });

  it('VCG runs a house deficit and conserves (nets + house = 0)', () => {
    const b = computeBalances([doneRecurring], people, prefsByChore, 'vcg');
    expect(b.houseCents).toBe(900);
    expect(b.nets.reduce((s, n) => s + n.net_cents, 0) + b.houseCents).toBe(0);
  });
});

describe('EMA financing (with VCG)', () => {
  // Blair is the doer, owed 900/week; the weekly house deficit is 900.
  const prefsByChore = { 5: { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) } };
  const week = (id: number, week_start: string): RawInstance => ({
    id, recurring_chore_id: 5, assignee_id: null, status: 'done', payout_cents: 0, week_start,
  });
  const financed = (weeks: RawInstance[]) =>
    computeBalances(weeks, people, prefsByChore, 'vcg', {}, [], 'ema');

  it('matches plain VCG for a single week (no history -> no levy yet)', () => {
    const b = financed([week(1, '2026-06-07')]);
    const plain = computeBalances([week(1, '2026-06-07')], people, prefsByChore, 'vcg');
    expect(b.nets).toEqual(plain.nets);
    expect(b.houseCents).toBe(900);
    expect(b.weeklyRateCents).toBe(Math.round(900 * 1.025)); // rate for next week
  });

  it('levies the marked-up EMA on later weeks and always conserves', () => {
    const b = financed([week(1, '2026-06-07'), week(2, '2026-06-14'), week(3, '2026-06-21')]);
    // Weeks 2 and 3 each levy round(900*1.025) -> per member; week 1 none.
    const per = Math.round(Math.round(900 * 1.025) / 3);
    expect(b.nets.find((n) => n.id === 1)?.net_cents).toBe(2 * per); // Alex: levy only
    expect(b.nets.find((n) => n.id === 3)?.net_cents).toBe(2 * per); // Casey: levy only
    // Blair: owed 2700 in entitlements, less her own two levies.
    expect(b.nets.find((n) => n.id === 2)?.net_cents).toBe(-2700 + 2 * per);
    // Conservation holds with the house as residual counterparty.
    expect(b.nets.reduce((s, n) => s + n.net_cents, 0) + b.houseCents).toBe(0);
  });

  it('the markup erodes the deficit into a surplus over a long run', () => {
    const weeks = Array.from({ length: 60 }, (_, k) =>
      week(k + 1, new Date(Date.UTC(2026, 0, 4 + 7 * k)).toISOString().slice(0, 10)),
    );
    // Past the ~41-week break-even, over-collection shows up as a house surplus.
    expect(financed(weeks).houseCents).toBeLessThan(0);
  });

  it('projects a levy for an unsettled week without collecting it yet', () => {
    const done = [week(1, '2026-06-07'), week(2, '2026-06-14')];
    const pending: RawInstance = { ...week(3, '2026-06-21'), status: 'pending' };

    const { schedule } = computeFinancing([...done, pending], people, prefsByChore, 'vcg');
    const thisWeek = schedule.get('2026-06-21')!;
    // Known from history (trailing EMA), but flagged as not-yet-collected.
    expect(thisWeek.settled).toBe(false);
    expect(thisWeek.levyCents).toBe(Math.round(900 * 1.025));
    expect(thisWeek.perMemberCents).toBe(Math.round(Math.round(900 * 1.025) / 3));

    // The pending week's projected levy must not change anyone's balance.
    const withPending = financed([...done, pending]);
    const settledOnly = financed(done);
    expect(withPending.nets).toEqual(settledOnly.nets);
    expect(withPending.houseCents).toBe(settledOnly.houseCents);
  });

  it('amortizes the doer payout over following weeks (no same-week receipt)', () => {
    const weeks = [week(1, '2026-06-07'), week(2, '2026-06-14'), week(3, '2026-06-21')];
    const flow = computeFinancingCashflow(weeks, people, prefsByChore, 'vcg');
    const per = Math.round(Math.round(900 * 1.025) / 3);

    // Week 1: the doer is owed 900 but the pool is empty, so nobody moves cash.
    const w1 = flow.get('2026-06-07')!;
    expect(w1.byRoommate.get(2)).toBe(0);
    expect(w1.byRoommate.get(1)).toBe(0);

    // Week 2: the levy is collected and the house pays the week-1 IOU back out.
    const w2 = flow.get('2026-06-14')!.byRoommate;
    expect(w2.get(1)).toBe(-per); // non-doers just pay the levy
    expect(w2.get(3)).toBe(-per);
    expect(w2.get(2)).toBe(3 * per - per); // doer receives the whole pool, less own levy
  });

  it('credits this week’s allocated (pending) doer so the week is not all-negative', () => {
    const past = [week(1, '2026-06-07'), week(2, '2026-06-14'), week(3, '2026-06-21')];
    const current: RawInstance = { ...week(4, '2026-06-28'), status: 'pending' };
    const flow = computeFinancingCashflow([...past, current], people, prefsByChore, 'vcg');

    const cur = flow.get('2026-06-28')!;
    expect(cur.settled).toBe(false); // projected: this week isn't done
    // The allocated doer is owed and paid down from the pool, so they net positive...
    expect(cur.byRoommate.get(2)!).toBeGreaterThan(0);
    // ...while the others pay the cash levy. (Before the fix, everyone was negative.)
    expect(cur.byRoommate.get(1)!).toBeLessThan(0);
  });

  it('does not credit a failed chore', () => {
    const failed: RawInstance = { ...week(1, '2026-06-07'), status: 'failed' };
    const flow = computeFinancingCashflow([failed], people, prefsByChore, 'vcg');
    const w = flow.get('2026-06-07')!;
    expect([...w.byRoommate.values()].every((v) => v === 0)).toBe(true);
    expect(w.houseDeficitCents).toBe(0); // nothing owed, nothing assessed
  });

  it('accrues notional surplus uncapped while cash collection stays capped', () => {
    const weeks = Array.from({ length: 55 }, (_, k) =>
      week(k + 1, new Date(Date.UTC(2026, 0, 4 + 7 * k)).toISOString().slice(0, 10)),
    );
    const flow = computeFinancingCashflow(weeks, people, prefsByChore, 'vcg');
    const last = flow.get(new Date(Date.UTC(2026, 0, 4 + 7 * 54)).toISOString().slice(0, 10))!;

    // The notional balance has crossed into surplus (negative) and keeps growing,
    // even though the cash levy is capped at what the house still owes.
    expect(last.houseDeficitCents).toBeLessThan(0);
    // Once the backlog clears the doer is paid same-week (positive)...
    expect(last.byRoommate.get(2)!).toBeGreaterThan(0);
    // ...and non-doers pay strictly less than the full marked-up assessment; the
    // gap is the surplus that accrues notionally without changing hands.
    const notionalPerHead = Math.round(Math.round(900 * 1.025) / 3);
    expect(Math.abs(last.byRoommate.get(1)!)).toBeLessThan(notionalPerHead);
  });

  it('financing off leaves the textbook VCG deficit untouched', () => {
    const b = computeBalances([week(1, '2026-06-07'), week(2, '2026-06-14')], people, prefsByChore, 'vcg');
    expect(b.houseCents).toBe(1800);
    expect(b.weeklyRateCents).toBe(0);
  });
});

describe('membership windows', () => {
  const roster: Person[] = [
    { id: 1, name: 'Alex' },
    { id: 2, name: 'Blair' },
    { id: 3, name: 'Nathan', joinDate: '2026-06-01' },
  ];

  it('excludes a roommate from weeks before they joined', () => {
    expect(membersForWeek(roster, '2026-05-31').map((p) => p.id)).toEqual([1, 2]);
  });

  it('includes a roommate from their join week onward', () => {
    expect(membersForWeek(roster, '2026-06-07').map((p) => p.id)).toEqual([1, 2, 3]);
  });

  it('excludes a roommate after their leave date', () => {
    const left: Person[] = [
      { id: 1, name: 'A' },
      { id: 2, name: 'B', leaveDate: '2026-06-05' },
    ];
    expect(membersForWeek(left, '2026-06-07').map((p) => p.id)).toEqual([1]);
  });

  it('keeps non-members out of the chore transfer for that week', () => {
    const prefsByChore = { 5: { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) } };
    const earlyWeek: RawInstance = {
      id: 1, recurring_chore_id: 5, assignee_id: null, status: 'done', payout_cents: 0, week_start: '2026-05-31',
    };
    const b = computeBalances([earlyWeek], roster, prefsByChore, 'agv');
    // Nathan hadn't joined, so he carries no balance for that week.
    expect(b.nets.find((n) => n.id === 3)?.net_cents).toBe(0);
    expect(b.nets.reduce((s, n) => s + n.net_cents, 0)).toBe(0);
  });
});

describe('recorded payments', () => {
  const roster: Person[] = [
    { id: 1, name: 'Alex' },
    { id: 2, name: 'Blair' },
  ];

  it('moves net from payer to recipient without touching the house', () => {
    const b = computeBalances([], roster, {}, 'agv', {}, [
      { from_roommate_id: 1, to_roommate_id: 2, amount_cents: 500 },
    ]);
    expect(b.nets.find((n) => n.id === 1)?.net_cents).toBe(-500);
    expect(b.nets.find((n) => n.id === 2)?.net_cents).toBe(500);
    expect(b.houseCents).toBe(0);
  });
});
