import { useState } from 'react';
import { Alert, Badge, Button, ButtonGroup, Form, Table, ToggleButton } from 'react-bootstrap';

import { api, useAsync } from '../lib/api';

// All three mechanisms are exactly budget-balanced (every chore's transfers sum
// to zero — no house account), and all finance the doer by an equal per-head
// split of a single price. They differ in how the price is set and who decides
// whether the chore happens at all.
const MECHANISMS = [
  { value: 'first-best', label: 'First-Best', hint: 'Equal split + first-best: the lowest bidder does the chore whenever total WTP covers their bid, is paid their own bid, and everyone (doer included) chips in an equal share of it. Maximizes reported surplus — the efficiency benchmark — but not strategyproof: the doer profits by inflating their bid, and inflated WTP sways the go/no-go decision.' },
  { value: 'vickrey-majority', label: 'Vickrey + Majority', hint: 'Equal split + Vickrey + majority: the lowest bidder does the chore but is paid the second-lowest bid, so bidding your true cost is dominant on the supply side. The chore happens only if a strict majority’s WTP covers their equal share of that price — nobody can be dragged into funding something a majority doesn’t accept, but a cheap chore a minority loves can be voted down.' },
  { value: 'vickrey-faltings', label: 'Vickrey + FaltingsFair', hint: 'Equal split + Vickrey + FaltingsFair: the doer is again paid the second-lowest bid. The go/no-go decision is delegated to a “jury” of everyone except one roommate (drawn per chore instance), with zero-sum fairness side-payments keeping the vote truthful. Since money only moves when a chore happens, those side-payments are scaled by n/k (k = juries that would fund) so their expectation is unchanged — audited to be exactly as incentive-compatible as paying them unconditionally. The cost: with 1/n probability the decision ignores your WTP.' },
];

export default function AdminPage({ bump }: { bump: () => void }) {
  const { data, setData, error } = useAsync(() => api('/api/roommates'), []);
  const settings = useAsync(() => api('/api/settings'), []);
  const [name, setName] = useState('');

  const mechanism = settings.data?.mechanism ?? 'first-best';

  async function selectMechanism(value: string) {
    if (value === mechanism) return;
    const next = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ mechanism: value }) });
    settings.setData(next);
    bump();
  }

  async function addRoommate(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    const next = await api('/api/roommates', { method: 'POST', body: JSON.stringify({ name }) });
    setName('');
    setData(next);
    bump();
  }

  async function removeRoommate(roommateId: number) {
    const next = await api(`/api/roommates/${roommateId}`, { method: 'DELETE' });
    setData(next);
    bump();
  }

  async function updateDates(roommate: any, field: 'join' | 'leave', value: string) {
    const next = await api(`/api/roommates/${roommate.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        join_date: field === 'join' ? value || null : roommate.join_date || null,
        leave_date: field === 'leave' ? value || null : roommate.leave_date || null,
      }),
    });
    setData(next);
    bump();
  }

  async function addExamples() {
    const next = await api('/api/roommates/examples', { method: 'POST' });
    setData(next);
    bump();
  }

  async function resetMockData() {
    await api('/api/test/reset-mock-data', { method: 'POST' });
    const next = await api('/api/roommates');
    setData(next);
    bump();
  }

  return (
    <section className="panel">
      {error && <Alert variant="danger">{error}</Alert>}

      <div className="mechanism-setting">
        <div className="mechanism-heading">
          <span className="mechanism-title">Transfer mechanism</span>
          <ButtonGroup>
            {MECHANISMS.map((option) => (
              <ToggleButton
                key={option.value}
                id={`mechanism-${option.value}`}
                type="radio"
                variant="outline-primary"
                name="mechanism"
                value={option.value}
                checked={mechanism === option.value}
                onChange={() => selectMechanism(option.value)}
              >
                {option.label}
              </ToggleButton>
            ))}
          </ButtonGroup>
        </div>
        <p className="mechanism-hint">{MECHANISMS.find((m) => m.value === mechanism)?.hint}</p>
      </div>

      <Form onSubmit={addRoommate} className="toolbar">
        <Form.Group controlId="new-roommate">
          <Form.Label>Name</Form.Label>
          <Form.Control value={name} onChange={(event) => setName(event.target.value)} />
        </Form.Group>
        <Button type="submit">Add Roommate</Button>
        <Button variant="outline-secondary" type="button" onClick={addExamples}>
          Load Examples
        </Button>
        <Button variant="outline-danger" type="button" onClick={resetMockData}>
          Reset Mock Data
        </Button>
      </Form>

      <Table responsive hover className="align-middle mb-0">
        <thead>
          <tr>
            <th>Name</th>
            <th>Joined</th>
            <th>Left</th>
            <th>Status</th>
            <th className="text-end">Action</th>
          </tr>
        </thead>
        <tbody>
          {(data?.roommates || []).map((roommate: any) => (
            <tr key={roommate.id}>
              <td>{roommate.name}</td>
              <td>
                <Form.Control
                  type="date"
                  size="sm"
                  value={roommate.join_date ?? ''}
                  onChange={(event) => updateDates(roommate, 'join', event.target.value)}
                />
              </td>
              <td>
                <Form.Control
                  type="date"
                  size="sm"
                  value={roommate.leave_date ?? ''}
                  onChange={(event) => updateDates(roommate, 'leave', event.target.value)}
                />
              </td>
              <td>
                <Badge bg={roommate.active ? 'success' : 'secondary'}>
                  {roommate.active ? 'Active' : 'Left'}
                </Badge>
              </td>
              <td className="text-end">
                {roommate.active ? (
                  <Button size="sm" variant="outline-secondary" onClick={() => removeRoommate(roommate.id)}>
                    Remove
                  </Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </section>
  );
}
