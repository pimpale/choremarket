import { describe, expect, it } from 'vitest';

import {
  computeBalances,
  computeLedger,
  DEFAULT_UNSET_BID_CENTS,
  effectivePref,
  flatPayout,
  ledgerForInstance,
  membersForWeek,
  type Pref,
  type Person,
  type PrefHistoryByChore,
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

describe('first-best (the doer\'s own bid, split among the others)', () => {
  it('pays the doer their full bid, financed by the other roommates', () => {
    const led = computeLedger(people, richPrefs, 'first-best');
    // The doer is exempt from their own price: the others each pay 700/2 and
    // the doer nets the full 700. balancedRound keeps the sum exact.
    expect(led.payments).toEqual({ 1: 350, 2: -700, 3: 350 });
    expect(paymentSum(led.payments)).toBe(0);
    expect(led.detail?.priceCents).toBe(700);
    expect(led.detail?.priceSetterId).toBeNull(); // first price: the doer's own bid
  });

  it('never charges the doer a share of their own price ($10 bid, 5 roommates -> $10)', () => {
    const five: Person[] = [1, 2, 3, 4, 5].map((id) => ({ id, name: `P${id}` }));
    const prefs = { 1: P(500, 1000), 2: P(500, 2000), 3: P(500, 2000), 4: P(500, 2000), 5: P(500, 2000) };
    const led = computeLedger(five, prefs, 'first-best');
    expect(led.assigneeId).toBe(1);
    expect(-led.payments[1]).toBe(1000); // the full $10, not $8
    expect(paymentSum(led.payments)).toBe(0);
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
  it('pays the doer the full second-lowest bid, split among the others, when a majority accepts', () => {
    const led = computeLedger(people, richPrefs, 'vickrey-majority');
    expect(led.assigneeId).toBe(2);
    // Price = 900 (Alex's bid). The system prices the chore at 900 * 3/2, so
    // the per-head share is 450: the vote tests WTP against it (everyone
    // covers it), the two non-doers pay it, and the doer nets the full 900.
    expect(led.payments).toEqual({ 1: 450, 2: -900, 3: 450 });
    expect(paymentSum(led.payments)).toBe(0);
    expect(led.detail).toMatchObject({
      priceCents: 900,
      priceSetterId: 1,
      shareCents: 450,
      supporters: 3,
      required: 2,
    });
  });

  it('skips when no strict majority accepts the per-head share, even at positive surplus', () => {
    // Surplus is 350 - 700 < 0 here anyway, but the binding reason is the vote:
    // share = 450 and nobody's WTP reaches it.
    const led = computeLedger(people, { 1: P(100, 900), 2: P(50, 700), 3: P(200, 1100) }, 'vickrey-majority');
    expect(led.worthDoing).toBe(false);
    expect(led.payments).toEqual({});
    expect(led.detail?.supporters).toBe(0);
    expect(led.skipReason).toMatch(/majority|accept/);
  });

  it('funds a negative-surplus chore when a majority accepts the share', () => {
    // With 3 people a majority covering the price/(n-1) share implies positive
    // surplus, so this takes 5: price = 800, share = 200, three of five accept
    // it while total WTP 630 < the doer's 700 bid.
    const five: Person[] = [1, 2, 3, 4, 5].map((id) => ({ id, name: `P${id}` }));
    const prefs = { 1: P(210, 700), 2: P(210, 800), 3: P(210, 1100), 4: P(0, 1100), 5: P(0, 1100) };
    const led = computeLedger(five, prefs, 'vickrey-majority');
    expect(led.surplusCents).toBeLessThan(0);
    expect(led.detail).toMatchObject({ priceCents: 800, shareCents: 200, supporters: 3, required: 3 });
    expect(led.worthDoing).toBe(true);
    expect(led.payments).toEqual({ 1: -800, 2: 200, 3: 200, 4: 200, 5: 200 });
  });

  it('forced assignee overrides a failed vote', () => {
    const led = computeLedger([people[0], people[1]], { 1: P(100, 900), 2: P(50, 700) }, 'vickrey-majority', 1);
    expect(led.worthDoing).toBe(true);
    expect(led.assigneeId).toBe(1);
    // Price = the other roommate's bid (700), paid entirely by them.
    expect(led.payments).toEqual({ 1: -700, 2: 700 });
  });
});

describe('vickrey-faltings (second price + jury draw + fairness side-payments)', () => {
  // Price = 900, system price = 1350, share = 450. netValues (wtp - 450):
  // Alex -350, Blair +250, Casey -150. Only the jury excluding Alex funds
  // (k = 1), and everyone carries nonzero fairness side-payments (computed
  // only from the others' reports).
  const pivotalPrefs = { 1: P(100, 500), 2: P(700, 900), 3: P(300, 1000) };

  it('reduces to the plain price split when every jury agrees and no one is pivotal', () => {
    for (const drawKey of [0, 1, 2]) {
      const led = computeLedger(people, richPrefs, 'vickrey-faltings', null, true, drawKey);
      expect(led.payments).toEqual({ 1: 450, 2: -900, 3: 450 });
      expect(led.incentivePaymentsCents).toEqual({ 1: 0, 2: 0, 3: 0 });
    }
  });

  it('funds, reporting the chore money and the scaled fairness incentives separately', () => {
    const led = computeLedger(people, pivotalPrefs, 'vickrey-faltings', null, true, 0);
    expect(led.assigneeId).toBe(1); // Alex bids 500
    expect(led.detail).toMatchObject({ priceCents: 900, excludedId: 1, juryFunds: true, fundingJuries: 1 });
    // Chore money: Blair and Casey finance Alex's 900 at 450 each. Incentives
    // (expected pivot charges minus rebates, scaled by n/k = 3/1): Alex pays
    // 100, Blair pays 150, Casey receives 250.
    expect(led.chorePaymentsCents).toEqual({ 1: -900, 2: 450, 3: 450 });
    expect(led.incentivePaymentsCents).toEqual({ 1: 100, 2: 150, 3: -250 });
    // The net transfer is the sum of the two parts, each exactly balanced.
    expect(led.payments).toEqual({ 1: -800, 2: 600, 3: 200 });
    expect(paymentSum(led.chorePaymentsCents!)).toBe(0);
    expect(paymentSum(led.incentivePaymentsCents!)).toBe(0);
    expect(paymentSum(led.payments)).toBe(0);
  });

  it('scaling keeps expected payments equal to the unconditional mechanism', () => {
    // Across the three equiprobable draws, only the jury excluding Alex funds
    // (payments {-800, 600, 200}); the other two decline with none. The totals
    // match 3x the expected payments of the mechanism that pays fairness in
    // every branch (1 funded split {-900, 450, 450} + unscaled fairness
    // payments of {+33.33, +50, -83.33} in all three draws).
    const totals: Record<number, number> = { 1: 0, 2: 0, 3: 0 };
    for (const drawKey of [0, 1, 2]) {
      const led = computeLedger(people, pivotalPrefs, 'vickrey-faltings', null, true, drawKey);
      for (const [id, amount] of Object.entries(led.payments)) totals[Number(id)] += amount;
    }
    expect(totals).toEqual({ 1: -800, 2: 600, 3: 200 });
  });

  it('skips when the drawn jury values the chore below its price', () => {
    // Excluding Blair (the fan) leaves a jury with net value -500.
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
    expect(led.payments).toEqual({ 1: 450, 2: -900, 3: 450 });
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
    'nets to zero across roommates and only counts done (%s)',
    (mechanism) => {
      const b = computeBalances([doneRecurring, pendingRecurring], people, prefsByChore, mechanism);
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

describe('recurring preference history (effective-dated, future-only)', () => {
  // Alex: an original bid effective from the beginning of time, then an edit
  // made on 2026-07-01. Blair never bids for this chore.
  const history: PrefHistoryByChore = {
    5: {
      1: [
        { created_at: '1970-01-01 00:00:00', wtp_cents: 1500, bid_cents: 900 },
        { created_at: '2026-07-01 12:00:00', wtp_cents: 1500, bid_cents: 1300 },
      ],
    },
  };

  it('picks the latest edit that had landed by the end of the week', () => {
    // Week ending before the edit still sees the original bid...
    expect(effectivePref(history[5][1], '2026-06-21')).toEqual({ wtp_cents: 1500, bid_cents: 900 });
    // ...and a week ending after the edit sees the new one.
    expect(effectivePref(history[5][1], '2026-07-05')).toEqual({ wtp_cents: 1500, bid_cents: 1300 });
  });

  it('treats an unset recurring bid as a very large ask (never auto-assigned)', () => {
    const base = { recurring_chore_id: 5, assignee_id: null, status: 'pending', payout_cents: 0 } as const;
    // Blair (id 2) has no history entry; only Alex can be the doer.
    const led = ledgerForInstance(
      { ...base, id: 1, week_start: '2026-07-05' },
      people,
      {},
      'first-best',
      {},
      history,
    );
    expect(led.assigneeId).toBe(1);
  });

  it('resolves each week from the value effective then (past weeks unchanged by later edits)', () => {
    const base = { recurring_chore_id: 5, assignee_id: 1, status: 'done', payout_cents: 0 } as const;
    const early = ledgerForInstance({ ...base, id: 1, week_start: '2026-06-21' }, people, {}, 'first-best', {}, history);
    const late = ledgerForInstance({ ...base, id: 2, week_start: '2026-07-05' }, people, {}, 'first-best', {}, history);
    // first-best pays the doer their own bid: 900 back then, 1300 after the edit.
    expect(early.detail?.priceCents).toBe(900);
    expect(late.detail?.priceCents).toBe(1300);
  });

  it('DEFAULT_UNSET_BID_CENTS is the shared one-off/recurring unset bid', () => {
    expect(DEFAULT_UNSET_BID_CENTS).toBe(100_000_000);
  });
});

describe('recorded payments', () => {
  const roster: Person[] = [
    { id: 1, name: 'Alex' },
    { id: 2, name: 'Blair' },
  ];

  it('moves net from payer to recipient, netting to zero', () => {
    const b = computeBalances([], roster, {}, 'first-best', {}, [
      { from_roommate_id: 1, to_roommate_id: 2, amount_cents: 500 },
    ]);
    expect(b.nets.find((n) => n.id === 1)?.net_cents).toBe(-500);
    expect(b.nets.find((n) => n.id === 2)?.net_cents).toBe(500);
    expect(b.nets.reduce((s, n) => s + n.net_cents, 0)).toBe(0);
  });
});
