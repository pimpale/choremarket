import { Alert, Col, Row, Table } from 'react-bootstrap';

import { api, cents, paymentClass, useAsync } from '../lib/api';

export default function BalancesPage({ refreshToken }: { refreshToken: number }) {
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
              {(data?.nets || []).map((row: any) => (
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
              {(data?.settlements || []).map((row: any) => (
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
