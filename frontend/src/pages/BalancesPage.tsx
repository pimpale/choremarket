import { useState } from 'react';
import { Alert, Button, Col, Form, Row, Tab, Table, Tabs } from 'react-bootstrap';

import { api, cents, dollarsToCents, paymentClass, useAsync } from '../lib/api';
import { computeBalances, type Person, type RawInstance } from '../lib/mechanism';

export default function BalancesPage({ refreshToken }: { refreshToken: number }) {
  const { data, setData, error } = useAsync(() => api('/api/ledger'), [refreshToken]);
  const [form, setForm] = useState({ from: '', to: '', amount: '', note: '' });
  const [formError, setFormError] = useState('');

  if (error) return <Alert variant="danger">{error}</Alert>;

  const mechanism = data?.mechanism ?? 'agv';
  const allRoommates = data?.roommates ?? [];
  const currentMembers = allRoommates.filter((r: any) => r.active);
  const people: Person[] = allRoommates.map((r: any) => ({
    id: r.id,
    name: r.name,
    joinDate: r.join_date,
    leaveDate: r.leave_date,
  }));
  const instances: RawInstance[] = (data?.instances ?? []).map((i: any) => ({
    id: i.id,
    recurring_chore_id: i.recurring_chore_id,
    assignee_id: i.assignee_id,
    status: i.status,
    payout_cents: i.payout_cents ?? 0,
    manual_override: Boolean(i.manual_override),
    week_start: i.week_start,
  }));
  const recordedPayments = data?.recorded_payments ?? [];

  // Balances are solved entirely on the client from the raw instances, prefs, and
  // recorded settle-up payments.
  const { nets, settlements, houseCents } = computeBalances(
    instances,
    people,
    data?.preferences_by_chore ?? {},
    mechanism,
    data?.preferences_by_instance ?? {},
    recordedPayments,
  );

  // Positive house net = the house pays out (deficit); negative = surplus.
  const houseLabel = houseCents > 0 ? 'deficit' : houseCents < 0 ? 'surplus' : 'balanced';

  async function recordPayment(event: React.FormEvent) {
    event.preventDefault();
    setFormError('');
    const amountCents = dollarsToCents(form.amount);
    if (!form.from || !form.to || form.from === form.to || amountCents <= 0) {
      setFormError('Pick two different roommates and a positive amount.');
      return;
    }
    try {
      const next = await api('/api/payments', {
        method: 'POST',
        body: JSON.stringify({
          from_roommate_id: Number(form.from),
          to_roommate_id: Number(form.to),
          amount_cents: amountCents,
          note: form.note,
        }),
      });
      setData(next);
      setForm({ from: '', to: '', amount: '', note: '' });
    } catch (err: any) {
      setFormError(err.message);
    }
  }

  async function deletePayment(id: number) {
    const next = await api(`/api/payments/${id}`, { method: 'DELETE' });
    setData(next);
  }

  return (
    <section className="panel">
      <Tabs defaultActiveKey="balances" className="mb-3">
        <Tab eventKey="balances" title="Balances">
          <div className="house-account">
            <div className="house-account-main">
              <span className="house-account-title">House account</span>
              <span className={`house-account-amount ${paymentClass(houseCents)}`}>{cents(houseCents)}</span>
              <span className="house-account-label">{houseLabel}</span>
            </div>
            <p className="house-account-note">
              Mechanism: <strong>{mechanism.toUpperCase()}</strong>. The house absorbs any imbalance that the
              roommate transfers don’t cover — always $0.00 under AGV, and the running deficit/surplus under VCG.
              Net balances already fold in recorded settle-up payments.
            </p>
          </div>

          <Row className="g-4">
            <Col lg={6}>
              <h2>Net Ledger</h2>
              <Table responsive hover className="align-middle">
                <thead>
                  <tr>
                    <th>Roommate</th>
                    <th>Net</th>
                  </tr>
                </thead>
                <tbody>
                  {nets.map((row) => (
                    <tr key={row.id}>
                      <td>{row.name}</td>
                      <td className={paymentClass(row.net_cents)}>{cents(row.net_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Col>
            <Col lg={6}>
              <h2>Settle Up</h2>
              <Table responsive hover className="align-middle">
                <thead>
                  <tr>
                    <th>From</th>
                    <th>To</th>
                    <th>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {settlements.map((row) => (
                    <tr key={`${row.from}:${row.to}:${row.amount_cents}`}>
                      <td>{row.from}</td>
                      <td>{row.to}</td>
                      <td>{cents(row.amount_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Col>
          </Row>
        </Tab>

        <Tab eventKey="payments" title="Payments">
          <p className="status-text mb-3">
            Record a real-world payment from one roommate to another. It immediately reduces the payer’s
            balance and the recipient’s credit.
          </p>
          {formError && <Alert variant="danger">{formError}</Alert>}
          <Form onSubmit={recordPayment} className="toolbar">
            <Form.Group controlId="payment-from">
              <Form.Label>From</Form.Label>
              <Form.Select value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })}>
                <option value="">Select…</option>
                {currentMembers.map((r: any) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </Form.Select>
            </Form.Group>
            <Form.Group controlId="payment-to">
              <Form.Label>To</Form.Label>
              <Form.Select value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })}>
                <option value="">Select…</option>
                {currentMembers.map((r: any) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </Form.Select>
            </Form.Group>
            <Form.Group controlId="payment-amount">
              <Form.Label>Amount</Form.Label>
              <Form.Control
                className="money-input"
                type="number"
                min="0"
                step="0.01"
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: e.target.value })}
              />
            </Form.Group>
            <Form.Group controlId="payment-note" className="grow">
              <Form.Label>Note</Form.Label>
              <Form.Control value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
            </Form.Group>
            <Button type="submit">Record Payment</Button>
          </Form>

          <Table responsive hover className="align-middle mb-0">
            <thead>
              <tr>
                <th>Date</th>
                <th>From</th>
                <th>To</th>
                <th>Amount</th>
                <th>Note</th>
                <th aria-label="delete"></th>
              </tr>
            </thead>
            <tbody>
              {recordedPayments.length === 0 ? (
                <tr>
                  <td colSpan={6} className="text-muted">No payments recorded yet.</td>
                </tr>
              ) : (
                recordedPayments.map((p: any) => (
                  <tr key={p.id}>
                    <td>{p.paid_on}</td>
                    <td>{p.from_name}</td>
                    <td>{p.to_name}</td>
                    <td>{cents(p.amount_cents)}</td>
                    <td>{p.note}</td>
                    <td className="text-end">
                      <Button size="sm" variant="outline-secondary" onClick={() => deletePayment(p.id)}>
                        Delete
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </Table>
        </Tab>
      </Tabs>
    </section>
  );
}
