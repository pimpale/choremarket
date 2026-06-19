import { useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  ButtonGroup,
  Col,
  Container,
  Form,
  Nav,
  Navbar,
  Row,
  Stack,
  Tab,
  Table,
  Tabs,
} from 'react-bootstrap';

const FREQUENCIES = ['monthly', 'weekly', 'one-off'];
const PAGE_TITLES = {
  admin: 'Admin',
  balances: 'Overall Balances',
  chores: 'Chore List',
  preferences: 'Roommate Preferences',
  ledger: 'Ledger',
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function cents(value) {
  const amount = Number(value || 0);
  const sign = amount < 0 ? '-' : '';
  const absolute = Math.abs(amount);
  return `${sign}$${Math.floor(absolute / 100)}.${String(absolute % 100).padStart(2, '0')}`;
}

function dollarsToCents(value) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.round(parsed * 100);
}

function centsToDollars(value) {
  return (Number(value || 0) / 100).toFixed(2);
}

function addWeek(weekStart) {
  const next = new Date(`${weekStart}T00:00:00`);
  next.setDate(next.getDate() + 7);
  return next.toISOString().slice(0, 10);
}

function weekHasPassed(weekStart) {
  const weekDate = new Date(`${weekStart}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return weekDate < today;
}

function useAsync(load, deps) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    setError('');
    load()
      .then((nextData) => {
        if (alive) {
          setData(nextData);
        }
      })
      .catch((err) => {
        if (alive) {
          setError(err.message);
        }
      });
    return () => {
      alive = false;
    };
  }, deps);

  return { data, setData, error };
}

export default function App() {
  const [page, setPage] = useState('preferences');
  const [refreshToken, setRefreshToken] = useState(0);

  return (
    <>
      <Navbar bg="white" expand="lg" className="border-bottom app-nav" sticky="top">
        <Container fluid="xl">
          <Navbar.Brand className="brand">Choremarket</Navbar.Brand>
          <Navbar.Toggle aria-controls="main-nav" />
          <Navbar.Collapse id="main-nav">
            <Nav activeKey={page} onSelect={(key) => key && setPage(key)} className="ms-auto">
              {Object.entries(PAGE_TITLES).map(([key, title]) => (
                <Nav.Link key={key} eventKey={key}>
                  {title}
                </Nav.Link>
              ))}
            </Nav>
          </Navbar.Collapse>
        </Container>
      </Navbar>

      <Container fluid="xl" className="page-shell">
        <div className="page-heading">
          <h1>{PAGE_TITLES[page]}</h1>
        </div>

        {page === 'admin' && <AdminPage bump={() => setRefreshToken((value) => value + 1)} />}
        {page === 'balances' && <BalancesPage refreshToken={refreshToken} />}
        {page === 'chores' && <ChoresPage bump={() => setRefreshToken((value) => value + 1)} />}
        {page === 'preferences' && <PreferencesPage refreshToken={refreshToken} />}
        {page === 'ledger' && <LedgerPage refreshToken={refreshToken} bump={() => setRefreshToken((value) => value + 1)} />}
      </Container>
    </>
  );
}

function AdminPage({ bump }) {
  const { data, setData, error } = useAsync(() => api('/api/roommates'), []);
  const [name, setName] = useState('');

  async function addRoommate(event) {
    event.preventDefault();
    if (!name.trim()) return;
    const next = await api('/api/roommates', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    setName('');
    setData(next);
    bump();
  }

  async function removeRoommate(roommateId) {
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
          {(data?.roommates || []).map((roommate) => (
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

function ChoresPage({ bump }) {
  const { data, setData, error } = useAsync(() => api('/api/chores'), []);
  const [drafts, setDrafts] = useState({});
  const [newChore, setNewChore] = useState({ name: '', frequency: 'one-off', description: '' });
  const chores = data?.chores || [];

  useEffect(() => {
    setDrafts(Object.fromEntries(chores.map((chore) => [chore.id, chore])));
  }, [data]);

  async function addChore(event) {
    event.preventDefault();
    if (!newChore.name.trim()) return;
    const next = await api('/api/chores', {
      method: 'POST',
      body: JSON.stringify(newChore),
    });
    setNewChore({ name: '', frequency: 'one-off', description: '' });
    setData(next);
    bump();
  }

  async function saveChore(choreId, nextDraft = drafts[choreId]) {
    if (!nextDraft?.name?.trim()) return;
    const next = await api(`/api/chores/${choreId}`, {
      method: 'PATCH',
      body: JSON.stringify(nextDraft),
    });
    setData(next);
    bump();
  }

  async function removeChore(choreId) {
    const next = await api(`/api/chores/${choreId}`, { method: 'DELETE' });
    setData(next);
    bump();
  }

  function updateDraft(choreId, field, value) {
    setDrafts((current) => ({
      ...current,
      [choreId]: { ...current[choreId], [field]: value },
    }));
  }

  return (
    <section className="panel">
      {error && <Alert variant="danger">{error}</Alert>}
      <Form onSubmit={addChore} className="toolbar">
        <Form.Group controlId="new-chore-name">
          <Form.Label>Task</Form.Label>
          <Form.Control value={newChore.name} onChange={(event) => setNewChore({ ...newChore, name: event.target.value })} />
        </Form.Group>
        <Form.Group controlId="new-chore-frequency">
          <Form.Label>How Often</Form.Label>
          <Form.Select value={newChore.frequency} onChange={(event) => setNewChore({ ...newChore, frequency: event.target.value })}>
            {FREQUENCIES.map((frequency) => (
              <option key={frequency} value={frequency}>
                {frequency}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        <Form.Group controlId="new-chore-description" className="grow">
          <Form.Label>Description</Form.Label>
          <Form.Control value={newChore.description} onChange={(event) => setNewChore({ ...newChore, description: event.target.value })} />
        </Form.Group>
        <Button type="submit">Add Chore</Button>
      </Form>

      <Table responsive hover className="align-middle mb-0">
        <thead>
          <tr>
            <th>Task</th>
            <th>How Often</th>
            <th>Description</th>
            <th className="text-end">Action</th>
          </tr>
        </thead>
        <tbody>
          {chores.map((chore) => {
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
                  <Form.Select
                    value={draft.frequency}
                    onChange={(event) => {
                      const nextDraft = { ...draft, frequency: event.target.value };
                      setDrafts((current) => ({ ...current, [chore.id]: nextDraft }));
                      saveChore(chore.id, nextDraft);
                    }}
                  >
                    {FREQUENCIES.map((frequency) => (
                      <option key={frequency} value={frequency}>
                        {frequency}
                      </option>
                    ))}
                  </Form.Select>
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

function PreferencesPage({ refreshToken }) {
  const [weekStart, setWeekStart] = useState('');
  const [activeKey, setActiveKey] = useState('');
  const { data, setData, error } = useAsync(
    () => api(`/api/preferences${weekStart ? `?week_start=${weekStart}` : ''}`),
    [weekStart, refreshToken],
  );
  const [drafts, setDrafts] = useState({});
  const [saveState, setSaveState] = useState('');

  useEffect(() => {
    if (!data) return;
    setWeekStart((current) => current || data.week_start);
    setActiveKey((current) => current || String(data.roommates[0]?.id || ''));
    setDrafts(
      Object.fromEntries(
        data.preferences.map((pref) => [
          `${pref.roommate_id}:${pref.chore_id}`,
          {
            ...pref,
            wtp_value: centsToDollars(pref.wtp_cents),
            bid_value: centsToDollars(pref.bid_cents),
          },
        ]),
      ),
    );
  }, [data]);

  async function savePreference(roommateId, choreId) {
    const draft = drafts[`${roommateId}:${choreId}`];
    if (!draft) return;
    setSaveState('Saving');
    await api('/api/preferences', {
      method: 'PUT',
      body: JSON.stringify({
        roommate_id: roommateId,
        chore_id: choreId,
        week_start: weekStart,
        wtp_cents: dollarsToCents(draft.wtp_value),
        bid_cents: dollarsToCents(draft.bid_value),
      }),
    });
    setSaveState('Saved');
  }

  function updatePreference(roommateId, choreId, field, value) {
    setDrafts((current) => {
      const key = `${roommateId}:${choreId}`;
      return {
        ...current,
        [key]: {
          ...(current[key] || { roommate_id: roommateId, chore_id: choreId, week_start: weekStart }),
          [field]: value,
        },
      };
    });
  }

  if (error) return <Alert variant="danger">{error}</Alert>;

  return (
    <section className="panel">
      <Stack direction="horizontal" gap={3} className="mb-3 flex-wrap">
        <Form.Group controlId="preferences-week">
          <Form.Label>Week to Edit</Form.Label>
          <Form.Control type="date" value={weekStart} onChange={(event) => setWeekStart(event.target.value)} />
        </Form.Group>
        <ButtonGroup className="align-self-end">
          <Button variant="outline-secondary" onClick={() => setWeekStart(addWeek(weekStart || data?.week_start))}>
            Next Week
          </Button>
        </ButtonGroup>
        {saveState ? <span className="status-text align-self-end">{saveState}</span> : null}
      </Stack>

      <Tabs activeKey={activeKey} onSelect={(key) => setActiveKey(key || '')} className="mb-3" justify>
        {(data?.roommates || []).map((roommate) => (
          <Tab key={roommate.id} eventKey={String(roommate.id)} title={roommate.name}>
            <PreferenceTable
              roommate={roommate}
              chores={data.chores}
              selectedWeek={weekStart || data.week_start}
              drafts={drafts}
              history={data.history[String(roommate.id)] || []}
              updatePreference={updatePreference}
              savePreference={savePreference}
            />
          </Tab>
        ))}
      </Tabs>
    </section>
  );
}

function PreferenceTable({ roommate, chores, selectedWeek, drafts, history, updatePreference, savePreference }) {
  const historicalRows = history.flatMap((week) =>
    week.preferences.map((pref) => ({
      week_start: week.week_start,
      chore_id: pref.chore_id,
      chore_name: pref.chore_name,
      chore_frequency: pref.chore_frequency,
      wtp_cents: pref.wtp_cents,
      bid_cents: pref.bid_cents,
      source_week: week.week_start,
    })),
  );
  const hasSelectedWeek = historicalRows.some((row) => row.week_start === selectedWeek);
  const selectedRows = hasSelectedWeek
    ? []
    : chores.map((chore) => {
        const draft = drafts[`${roommate.id}:${chore.id}`] || {};
        return {
          week_start: selectedWeek,
          chore_id: chore.id,
          chore_name: chore.name,
          chore_frequency: chore.frequency,
          wtp_value: draft.wtp_value ?? '0.00',
          bid_value: draft.bid_value ?? '0.00',
          wtp_cents: draft.wtp_cents,
          bid_cents: draft.bid_cents,
          source_week: draft.source_week,
          editable: !weekHasPassed(selectedWeek),
        };
      });
  const rows = [...historicalRows, ...selectedRows].sort((a, b) => {
    if (a.week_start === b.week_start) {
      return a.chore_name.localeCompare(b.chore_name);
    }
    return a.week_start.localeCompare(b.week_start);
  });

  return (
    <Table responsive hover bordered size="sm" className="align-middle ledger-table mb-0">
      <thead>
        <tr>
          <th>Week</th>
          <th>Chore</th>
          <th>How Often</th>
          <th>WTP</th>
          <th>Bid</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const locked = !row.editable;
          return (
            <tr key={`${row.week_start}:${roommate.id}:${row.chore_id}`} className={locked ? 'locked-row' : ''}>
              <td>{row.week_start}</td>
              <td>{row.chore_name}</td>
              <td>{row.chore_frequency}</td>
              <td>
                {locked ? (
                  cents(row.wtp_cents)
                ) : (
                  <Form.Control
                    className="money-input"
                    type="number"
                    min="0"
                    step="0.01"
                    value={row.wtp_value ?? centsToDollars(row.wtp_cents)}
                    onChange={(event) => updatePreference(roommate.id, row.chore_id, 'wtp_value', event.target.value)}
                    onBlur={() => savePreference(roommate.id, row.chore_id)}
                  />
                )}
              </td>
              <td>
                {locked ? (
                  cents(row.bid_cents)
                ) : (
                  <Form.Control
                    className="money-input"
                    type="number"
                    min="0"
                    step="0.01"
                    value={row.bid_value ?? centsToDollars(row.bid_cents)}
                    onChange={(event) => updatePreference(roommate.id, row.chore_id, 'bid_value', event.target.value)}
                    onBlur={() => savePreference(roommate.id, row.chore_id)}
                  />
                )}
              </td>
              <td>{locked ? 'Locked' : row.source_week ? `Using ${row.source_week}` : 'Editable'}</td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

function LedgerPage({ refreshToken, bump }) {
  const [weekStart, setWeekStart] = useState('');
  const { data, setData, error } = useAsync(
    () => api(`/api/ledger${weekStart ? `?week_start=${weekStart}` : ''}`),
    [weekStart, refreshToken],
  );

  useEffect(() => {
    if (data?.week_start) {
      setWeekStart((current) => current || data.week_start);
    }
  }, [data]);

  async function recordWeek() {
    const next = await api('/api/ledger/run', {
      method: 'POST',
      body: JSON.stringify({ week_start: weekStart }),
    });
    setData(next);
    bump();
  }

  if (error) return <Alert variant="danger">{error}</Alert>;

  const ledgerRows = (data?.history_weeks || []).flatMap((week) =>
    week.entries.map((entry) => ({ ...entry, week_start: week.week_start })),
  );

  return (
    <section className="panel">
      <Stack direction="horizontal" gap={3} className="mb-3 flex-wrap">
        <Form.Group controlId="ledger-week">
          <Form.Label>Week to Record</Form.Label>
          <Form.Control type="date" value={weekStart} onChange={(event) => setWeekStart(event.target.value)} />
        </Form.Group>
        <ButtonGroup className="align-self-end">
          <Button variant="outline-secondary" onClick={() => setWeekStart(addWeek(weekStart || data?.week_start))}>
            Next Week
          </Button>
          <Button onClick={recordWeek}>Record Week</Button>
        </ButtonGroup>
      </Stack>

      {ledgerRows.length ? (
        <Table responsive hover bordered size="sm" className="align-middle ledger-table mb-0">
          <thead>
            <tr>
              <th>Week / Penalty</th>
              <th>Roommate</th>
              <th>Chore</th>
              <th>Frequency</th>
              <th>Due Date</th>
              <th>Cost</th>
              <th>AGV Payments</th>
            </tr>
          </thead>
          <tbody>
            {ledgerRows.map((entry, index) => (
              <tr key={`${entry.week_start}:${entry.chore_name}:${entry.assignee_name}:${index}`}>
                <td>{entry.week_start}</td>
                <td>{entry.assignee_name || 'Unassigned'}</td>
                <td>{entry.chore_name}</td>
                <td>{entry.sheet_frequency || entry.frequency}</td>
                <td>{entry.due_date || ''}</td>
                <td>{entry.listed_cost || ''}</td>
                <td>
                  {entry.payments.map((payment) => (
                    <span key={`${entry.week_start}:${entry.chore_name}:${payment.roommate_name}`} className={`payment-chip ${paymentClass(payment.amount_cents)}`}>
                      {payment.roommate_name} {cents(payment.amount_cents)}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <Alert variant="secondary">No recorded prior weeks yet.</Alert>
      )}
    </section>
  );
}

function BalancesPage({ refreshToken }) {
  const { data, error } = useAsync(() => api('/api/balances'), [refreshToken]);

  if (error) return <Alert variant="danger">{error}</Alert>;

  return (
    <section className="panel">
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
              {(data?.nets || []).map((row) => (
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
              {(data?.settlements || []).map((row) => (
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
    </section>
  );
}

function paymentClass(amount) {
  if (amount > 0) return 'pay';
  if (amount < 0) return 'receive';
  return '';
}
