import { useState } from 'react';
import { Alert, Badge, Button, ButtonGroup, Form, Table, ToggleButton } from 'react-bootstrap';

import { api, useAsync } from '../lib/api';

const MECHANISMS = [
  { value: 'agv', label: 'AGV', hint: 'Budget-balanced: every chore’s transfers sum to zero — no house account needed.' },
  { value: 'vcg', label: 'VCG', hint: 'Vickrey–Clarke–Groves: the doer is paid the second-lowest bid; the house covers the resulting deficit.' },
  { value: 'bailey-cavallo', label: 'Bailey–Cavallo', hint: 'Symmetric Cavallo: each roommate’s transfer is adjusted by 1/n of the VCG revenue the others would generate without them — a rebate when that’s a surplus, a charge when it’s a deficit. Strategyproof like VCG, and it shares the house deficit back among roommates (so it isn’t individually rational, and only approaches budget balance — the house keeps a smaller residual).' },
];

const FINANCINGS = [
  { value: 'none', label: 'None', hint: 'The house absorbs any imbalance directly — $0 under AGV, a running deficit under VCG.' },
  { value: 'ema', label: 'EMA', hint: 'Amortize the deficit instead of absorbing it: each settled week everyone pays a flat levy (a marked-up EMA of past deficits), which pays the doers down over the following weeks. The house never pays out of pocket and trends toward a small, burnable surplus.' },
];

export default function AdminPage({ bump }: { bump: () => void }) {
  const { data, setData, error } = useAsync(() => api('/api/roommates'), []);
  const settings = useAsync(() => api('/api/settings'), []);
  const [name, setName] = useState('');

  const mechanism = settings.data?.mechanism ?? 'agv';
  const financing = settings.data?.financing ?? 'none';

  async function selectMechanism(value: string) {
    if (value === mechanism) return;
    const next = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ mechanism: value }) });
    settings.setData(next);
    bump();
  }

  async function selectFinancing(value: string) {
    if (value === financing) return;
    const next = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ financing: value }) });
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

      <div className="mechanism-setting">
        <div className="mechanism-heading">
          <span className="mechanism-title">Financing</span>
          <ButtonGroup>
            {FINANCINGS.map((option) => (
              <ToggleButton
                key={option.value}
                id={`financing-${option.value}`}
                type="radio"
                variant="outline-primary"
                name="financing"
                value={option.value}
                checked={financing === option.value}
                onChange={() => selectFinancing(option.value)}
              >
                {option.label}
              </ToggleButton>
            ))}
          </ButtonGroup>
        </div>
        <p className="mechanism-hint">{FINANCINGS.find((f) => f.value === financing)?.hint}</p>
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
