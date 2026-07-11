import { describe, expect, it } from 'vitest';

import {
  computeBalances,
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

// Blair is the cheapest (bid 700); the Vickrey price is Alex's 900.
const richPrefs = { 1: P(1500, 900), 2: P(1200, 700), 3: P(1800, 1100) };

const paymentSum = (payments: Record<number, number>) =>
  Object.values(payments).reduce((s, a) => s + a, 0);

describe('assignment', () => {
  it('assigns the lowest bidder', () => {
    const led = computeLedger(people, richPrefs, 'first-best');
    expect(led.assigneeId).toBe(2);
  });

  it('breaks bid ties by name then id', () => {
    const two = [
      { id: 1, name: 'Blair' },
      { id: 2, name: 'Alex' },
    ];
    const led = computeLedger(two, { 1: P(1000, 500), 2: P(1000, 500) }, 'first-best');
    expect(led.assigneeId).toBe(2); // "Alex" < "Blair"
  });
});

describe('first-best (equal split of the doer\'s own bid)', () => {
  it('pays the doer their bid, financed by an equal per-head split', () => {
    const led = computeLedger(people, richPrefs, 'first-best');
    // Share = 700/3; the doer nets 700 - share. balancedRound keeps the sum exact.
    expect(led.payments).toEqual({ 1: 233, 2: -467, 3: 234 });
    expect(paymentSum(led.payments)).toBe(0);
    expect(led.detail?.priceCents).toBe(700);
    expect(led.detail?.priceSetterId).toBeNull(); // first price: the doer's own bid
  });

  it('skips when total WTP is below the lowest bid', () => {
    const led = computeLedger(people, { 1: P(100, 900), 2: P(200, 700), 3: P(150, 1100) }, 'first-best');
    expect(led.worthDoing).toBe(false);
    expect(led.assigneeId).toBeNull();
    expect(led.payments).toEqual({});
    expect(led.surplusCents).toBe(450 - 700);
    expect(led.skipReason).toMatch(/WTP/);
  });
});

describe('vickrey-majority (second price + strict-majority funding)', () => {
  it('pays the doer the second-lowest bid, split equally, when a majority accepts the share', () => {
    const led = computeLedger(people, richPrefs, 'vickrey-majority');
    expect(led.assigneeId).toBe(2);
    // Price = 900 (Alex's bid), share = 300, everyone's WTP covers it.
    expect(led.payments).toEqual({ 1: 300, 2: -600, 3: 300 });
    expect(paymentSum(led.payments)).toBe(0);
    expect(led.detail).toMatchObject({ priceCents: 900, priceSetterId: 1, shareCents: 300, supporters: 3, required: 2 });
  });

  it('skips when no strict majority accepts the per-head share, even at positive surplus', () => {
    // Surplus is 350 - 700 < 0 here anyway, but the binding reason is the vote:
    // share = 300 and nobody's WTP reaches it.
    const led = computeLedger(people, { 1: P(100, 900), 2: P(50, 700), 3: P(200, 1100) }, 'vickrey-majority');
    expect(led.worthDoing).toBe(false);
    expect(led.payments).toEqual({});
    expect(led.detail?.supporters).toBe(0);
    expect(led.skipReason).toMatch(/majority|accept/);
  });

  it('funds a negative-surplus chore when a majority accepts the share', () => {
    // Total WTP 620 < bid 700, but two of three accept the 300 share.
    const led = computeLedger(people, { 1: P(310, 900), 2: P(310, 700), 3: P(0, 1100) }, 'vickrey-majority');
    expect(led.surplusCents).toBeLessThan(0);
    expect(led.worthDoing).toBe(true);
    expect(led.payments).toEqual({ 1: 300, 2: -600, 3: 300 });
  });

  it('forced assignee overrides a failed vote', () => {
    const led = computeLedger([people[0], people[1]], { 1: P(100, 900), 2: P(50, 700) }, 'vickrey-majority', 1);
    expect(led.worthDoing).toBe(true);
    expect(led.assigneeId).toBe(1);
    // Price = the other roommate's bid (700), split two ways.
    expect(led.payments).toEqual({ 1: -350, 2: 350 });
  });
});

describe('vickrey-faltings (second price + jury draw + fairness side-payments)', () => {
  // netValues (wtp - 300 share): Alex -200, Blair +400, Casey 0. The jury's
  // verdict depends on who is excluded, and Blair/Casey carry nonzero
  // fairness side-payments (computed only from the others' reports).
  const pivotalPrefs = { 1: P(100, 500), 2: P(700, 900), 3: P(300, 1000) };

  it('reduces to the plain equal split when every jury agrees and no one is pivotal', () => {
    for (const drawKey of [0, 1, 2]) {
      const led = computeLedger(people, richPrefs, 'vickrey-faltings', null, true, drawKey);
      expect(led.payments).toEqual({ 1: 300, 2: -600, 3: 300 });
    }
  });

  it('funds and folds the p-scaled fairness adjustments into the transfers', () => {
    const led = computeLedger(people, pivotalPrefs, 'vickrey-faltings', null, true, 0);
    expect(led.assigneeId).toBe(1); // Alex bids 500
    expect(led.detail).toMatchObject({ priceCents: 900, excludedId: 1, juryFunds: true, fundingJuries: 2 });
    // Blair's expected pivot charge is 200/3, scaled by n/k = 3/2 to the 100
    // she pays per funded draw; Casey receives it as a rebate.
    expect(led.payments).toEqual({ 1: -600, 2: 400, 3: 200 });
    expect(paymentSum(led.payments)).toBe(0);
  });

  it('scaling keeps expected payments equal to the unconditional mechanism', () => {
    // Across the three equiprobable draws, two fund with payments
    // {-600, 400, 200} and one declines with none. The totals match 3x the
    // expected payments of the mechanism that pays fairness in every branch
    // (2 funded splits + unscaled fairness of -0/+66.67/-66.67 in all three).
    const totals: Record<number, number> = { 1: 0, 2: 0, 3: 0 };
    for (const drawKey of [0, 1, 2]) {
      const led = computeLedger(people, pivotalPrefs, 'vickrey-faltings', null, true, drawKey);
      for (const [id, amount] of Object.entries(led.payments)) totals[Number(id)] += amount;
    }
    expect(totals).toEqual({ 1: -1200, 2: 800, 3: 400 });
  });

  it('skips when the drawn jury values the chore below its price', () => {
    // Excluding Blair (the fan) leaves a jury with net value -200.
    const led = computeLedger(people, pivotalPrefs, 'vickrey-faltings', null, true, 1);
    expect(led.detail?.excludedId).toBe(2);
    expect(led.worthDoing).toBe(false);
    expect(led.payments).toEqual({});
    expect(led.skipReason).toMatch(/jury/);
  });

  it('is exactly budget-balanced in every realized draw', () => {
    for (const drawKey of [0, 1, 2]) {
      const led = computeLedger(people, pivotalPrefs, 'vickrey-faltings', null, true, drawKey);
      expect(paymentSum(led.payments)).toBe(0);
    }
  });

  it('realizes the draw from the instance id', () => {
    const prefsByChore = { 5: pivotalPrefs };
    const base = { recurring_chore_id: 5, assignee_id: null, status: 'pending', payout_cents: 0 } as const;
    // id 3 -> excluded Alex (funds); id 1 -> excluded Blair (declines).
    expect(ledgerForInstance({ ...base, id: 3 }, people, prefsByChore, 'vickrey-faltings').worthDoing).toBe(true);
    expect(ledgerForInstance({ ...base, id: 1 }, people, prefsByChore, 'vickrey-faltings').worthDoing).toBe(false);
  });
});

describe('one-offs', () => {
  it('splits a flat payout to net zero', () => {
    expect(flatPayout(1, [1, 2, 3, 4], 900)).toEqual({ 1: -900, 2: 300, 3: 300, 4: 300 });
  });

  it('derives a one-off ledger from per-instance prefs', () => {
    const instance: RawInstance = { id: 9, recurring_chore_id: null, assignee_id: 2, status: 'pending', payout_cents: 1200 };
    const led = ledgerForInstance(instance, people, {}, 'vickrey-majority', { 9: richPrefs });
    expect(led.assigneeId).toBe(2);
    expect(led.payments).toEqual({ 1: 300, 2: -600, 3: 300 });
    expect(paymentSum(led.payments)).toBe(0);
  });

  it('defaults unset one-off prefs to no WTP and a very large bid', () => {
    const instance: RawInstance = { id: 10, recurring_chore_id: null, assignee_id: null, status: 'pending', payout_cents: 0 };
    const led = ledgerForInstance(instance, people, {}, 'first-best', {
      10: {
        1: { wtp_cents: null, bid_cents: null },
      },
    });
    expect(led.displayStatus).toBe('skipped');
    expect(led.surplusCents).toBe(-100_000_000);
  });

  it('does not force a one-off when selected assignee bid exceeds total WTP', () => {
    const instance: RawInstance = { id: 11, recurring_chore_id: null, assignee_id: 2, status: 'pending', payout_cents: 0 };
    const led = ledgerForInstance(instance, people, {}, 'first-best', {
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
    const led = ledgerForInstance(instance, people, {}, 'first-best');
    expect(led.assigneeId).toBe(1);
    expect(led.displayStatus).toBe('pending');
    expect(led.payments).toEqual({ 1: -900, 2: 450, 3: 450 });
  });
});

describe('display status', () => {
  it('derives skipped, but never overrides a manual done/failed', () => {
    const prefsByChore = { 5: { 1: P(100, 900), 2: P(200, 700), 3: P(150, 1100) } };
    const base = { id: 1, recurring_chore_id: 5, assignee_id: null, payout_cents: 0 } as const;
    expect(ledgerForInstance({ ...base, status: 'pending' }, people, prefsByChore, 'first-best').displayStatus).toBe('skipped');
    expect(ledgerForInstance({ ...base, status: 'done' }, people, prefsByChore, 'first-best').displayStatus).toBe('done');
  });
});

describe('balances', () => {
  const prefsByChore = { 5: richPrefs };
  const doneRecurring: RawInstance = { id: 1, recurring_chore_id: 5, assignee_id: null, status: 'done', payout_cents: 0 };
  const pendingRecurring: RawInstance = { ...doneRecurring, id: 2, status: 'pending' };

  it.each(['first-best', 'vickrey-majority', 'vickrey-faltings'] as const)(
    'nets to zero with a flat house and only counts done (%s)',
    (mechanism) => {
      const b = computeBalances([doneRecurring, pendingRecurring], people, prefsByChore, mechanism);
      expect(b.houseCents).toBe(0);
      expect(b.nets.reduce((s, n) => s + n.net_cents, 0)).toBe(0);
    },
  );
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
    const prefsByChore = { 5: richPrefs };
    const earlyWeek: RawInstance = {
      id: 1, recurring_chore_id: 5, assignee_id: null, status: 'done', payout_cents: 0, week_start: '2026-05-31',
    };
    const b = computeBalances([earlyWeek], roster, prefsByChore, 'first-best');
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
    const b = computeBalances([], roster, {}, 'first-best', {}, [
      { from_roommate_id: 1, to_roommate_id: 2, amount_cents: 500 },
    ]);
    expect(b.nets.find((n) => n.id === 1)?.net_cents).toBe(-500);
    expect(b.nets.find((n) => n.id === 2)?.net_cents).toBe(500);
    expect(b.houseCents).toBe(0);
  });
});
