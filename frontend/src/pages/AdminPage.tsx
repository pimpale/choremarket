import { useState } from 'react';
import { Alert, Badge, Button, Form, Table } from 'react-bootstrap';

import { api, useAsync } from '../lib/api';

export default function AdminPage({ bump }: { bump: () => void }) {
  const { data, setData, error } = useAsync(() => api('/api/roommates'), []);
  const [name, setName] = useState('');

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
            <th>Status</th>
            <th className="text-end">Action</th>
          </tr>
        </thead>
        <tbody>
          {(data?.roommates || []).map((roommate: any) => (
            <tr key={roommate.id}>
              <td>{roommate.name}</td>
              <td>
                <Badge bg={roommate.active ? 'success' : 'secondary'}>
                  {roommate.active ? 'Active' : 'Removed'}
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
