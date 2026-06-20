import { useEffect, useState } from 'react';
import { Alert, Form, Tab, Table, Tabs } from 'react-bootstrap';

import { api, centsToDollars, dollarsToCents, useAsync } from '../lib/api';

export default function PreferencesPage({
  refreshToken,
  bump,
}: {
  refreshToken: number;
  bump: () => void;
}) {
  const { data, error } = useAsync(() => api('/api/preferences'), [refreshToken]);
  const [activeKey, setActiveKey] = useState('');
  const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [saveState, setSaveState] = useState('');

  useEffect(() => {
    if (!data) return;
    setActiveKey((current) => current || String(data.roommates[0]?.id || ''));
    setDrafts(
      Object.fromEntries(
        data.preferences.map((pref: any) => [
          `${pref.roommate_id}:${pref.recurring_chore_id}`,
          {
            ...pref,
            wtp_value: centsToDollars(pref.wtp_cents),
            bid_value: centsToDollars(pref.bid_cents),
          },
        ]),
      ),
    );
  }, [data]);

  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Alert variant="secondary">Loading…</Alert>;

  function updateDraft(roommateId: number, choreId: number, field: string, value: string) {
    const key = `${roommateId}:${choreId}`;
    setDrafts((current) => ({
      ...current,
      [key]: { ...(current[key] || {}), [field]: value },
    }));
  }

  async function savePreference(roommateId: number, choreId: number) {
    const draft = drafts[`${roommateId}:${choreId}`] || {};
    setSaveState('Saving…');
    await api('/api/preferences', {
      method: 'PUT',
      body: JSON.stringify({
        roommate_id: roommateId,
        recurring_chore_id: choreId,
        wtp_cents: dollarsToCents(draft.wtp_value),
        bid_cents: dollarsToCents(draft.bid_value),
      }),
    });
    setSaveState('Saved');
    bump();
  }

  return (
    <section className="panel">
      <p className="status-text mb-3">
        Set each roommate&apos;s willingness-to-pay and bid for the recurring chores. These feed the
        weekly auto-assignment and the payments shown on the ledger. {saveState}
      </p>
      <Tabs activeKey={activeKey} onSelect={(key) => setActiveKey(key || '')} className="mb-3" justify>
        {data.roommates.map((roommate: any) => (
          <Tab key={roommate.id} eventKey={String(roommate.id)} title={roommate.name}>
            <Table responsive bordered size="sm" className="align-middle ledger-table mb-0">
              <thead>
                <tr>
                  <th>Recurring Chore</th>
                  <th>WTP</th>
                  <th>Bid</th>
                </tr>
              </thead>
              <tbody>
                {data.recurring_chores.map((chore: any) => {
                  const draft = drafts[`${roommate.id}:${chore.id}`] || {};
                  return (
                    <tr key={chore.id}>
                      <td>{chore.name}</td>
                      <td>
                        <Form.Control
                          className="money-input"
                          type="number"
                          min="0"
                          step="0.01"
                          value={draft.wtp_value ?? '0.00'}
                          onChange={(event) => updateDraft(roommate.id, chore.id, 'wtp_value', event.target.value)}
                          onBlur={() => savePreference(roommate.id, chore.id)}
                        />
                      </td>
                      <td>
                        <Form.Control
                          className="money-input"
                          type="number"
                          min="0"
                          step="0.01"
                          value={draft.bid_value ?? '0.00'}
                          onChange={(event) => updateDraft(roommate.id, chore.id, 'bid_value', event.target.value)}
                          onBlur={() => savePreference(roommate.id, chore.id)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </Tab>
        ))}
      </Tabs>
    </section>
  );
}
