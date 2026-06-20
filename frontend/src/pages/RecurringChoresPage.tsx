import { useEffect, useState } from 'react';
import { Alert, Button, Form, Table } from 'react-bootstrap';

import { api, useAsync } from '../lib/api';

export default function RecurringChoresPage({ bump }: { bump: () => void }) {
  const { data, setData, error } = useAsync(() => api('/api/recurring-chores'), []);
  const [drafts, setDrafts] = useState<Record<number, any>>({});
  const [newChore, setNewChore] = useState({ name: '', description: '' });
  const chores = data?.recurring_chores || [];

  useEffect(() => {
    setDrafts(Object.fromEntries(chores.map((chore: any) => [chore.id, chore])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  async function addChore(event: React.FormEvent) {
    event.preventDefault();
    if (!newChore.name.trim()) return;
    const next = await api('/api/recurring-chores', {
      method: 'POST',
      body: JSON.stringify(newChore),
    });
    setNewChore({ name: '', description: '' });
    setData(next);
    bump();
  }

  async function saveChore(choreId: number, nextDraft = drafts[choreId]) {
    if (!nextDraft?.name?.trim()) return;
    const next = await api(`/api/recurring-chores/${choreId}`, {
      method: 'PATCH',
      body: JSON.stringify(nextDraft),
    });
    setData(next);
    bump();
  }

  async function removeChore(choreId: number) {
    const next = await api(`/api/recurring-chores/${choreId}`, { method: 'DELETE' });
    setData(next);
    bump();
  }

  function updateDraft(choreId: number, field: string, value: string) {
    setDrafts((current) => ({
      ...current,
      [choreId]: { ...current[choreId], [field]: value },
    }));
  }

  if (error) return <Alert variant="danger">{error}</Alert>;

  return (
    <section className="panel">
      <p className="status-text mb-3">
        Recurring chores spawn one ledger row per roommate-week automatically.
      </p>
      <Form onSubmit={addChore} className="toolbar">
        <Form.Group controlId="new-chore-name">
          <Form.Label>Chore</Form.Label>
          <Form.Control value={newChore.name} onChange={(event) => setNewChore({ ...newChore, name: event.target.value })} />
        </Form.Group>
        <Form.Group controlId="new-chore-description" className="grow">
          <Form.Label>Description</Form.Label>
          <Form.Control value={newChore.description} onChange={(event) => setNewChore({ ...newChore, description: event.target.value })} />
        </Form.Group>
        <Button type="submit">Add Recurring Chore</Button>
      </Form>

      <Table responsive hover className="align-middle mb-0">
        <thead>
          <tr>
            <th>Chore</th>
            <th>Description</th>
            <th className="text-end">Action</th>
          </tr>
        </thead>
        <tbody>
          {chores.map((chore: any) => {
            const draft = drafts[chore.id] || chore;
            return (
              <tr key={chore.id}>
                <td>
                  <Form.Control
                    value={draft.name}
                    onChange={(event) => updateDraft(chore.id, 'name', event.target.value)}
                    onBlur={() => saveChore(chore.id)}
                  />
                </td>
                <td>
                  <Form.Control
                    value={draft.description}
                    onChange={(event) => updateDraft(chore.id, 'description', event.target.value)}
                    onBlur={() => saveChore(chore.id)}
                  />
                </td>
                <td className="text-end">
                  <Button size="sm" variant="outline-secondary" onClick={() => removeChore(chore.id)}>
                    Remove
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </section>
  );
}
