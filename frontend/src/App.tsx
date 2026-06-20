import { useState, type ReactNode } from 'react';
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { Container, Nav, Navbar } from 'react-bootstrap';

import LedgerPage from './pages/LedgerPage';
import PreferencesPage from './pages/PreferencesPage';
import RecurringChoresPage from './pages/RecurringChoresPage';
import AdminPage from './pages/AdminPage';
import BalancesPage from './pages/BalancesPage';

const PAGE_TITLES: Record<string, string> = {
  ledger: 'Ledger',
  preferences: 'Roommate Preferences',
  recurring: 'Recurring Chores',
  admin: 'Admin',
  balances: 'Overall Balances',
};

function PageContainer({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Container fluid="xl" className="page-shell">
      <div className="page-heading">
        <h1>{title}</h1>
      </div>
      {children}
    </Container>
  );
}

function AppShell() {
  const [refreshToken, setRefreshToken] = useState(0);
  const bump = () => setRefreshToken((value) => value + 1);
  const location = useLocation();
  const activeKey = location.pathname.split('/')[1] || 'ledger';
  const isLedger = activeKey === 'ledger';

  return (
    <div className={`app-root${isLedger ? ' app-root-full' : ''}`}>
      <Navbar bg="white" expand="lg" className="border-bottom app-nav">
        <Container fluid className="app-nav-container">
          <Navbar.Brand className="brand">Choremarket</Navbar.Brand>
          <Navbar.Toggle aria-controls="main-nav" />
          <Navbar.Collapse id="main-nav">
            <Nav activeKey={activeKey} className="ms-auto">
              {Object.entries(PAGE_TITLES).map(([key, title]) => (
                <Nav.Link key={key} as={NavLink} to={`/${key}`} eventKey={key}>
                  {title}
                </Nav.Link>
              ))}
            </Nav>
          </Navbar.Collapse>
        </Container>
      </Navbar>

      <Routes>
        <Route path="/ledger" element={<LedgerPage refreshToken={refreshToken} bump={bump} />} />
        <Route
          path="/preferences"
          element={
            <PageContainer title={PAGE_TITLES.preferences}>
              <PreferencesPage refreshToken={refreshToken} bump={bump} />
            </PageContainer>
          }
        />
        <Route
          path="/recurring"
          element={
            <PageContainer title={PAGE_TITLES.recurring}>
              <RecurringChoresPage bump={bump} />
            </PageContainer>
          }
        />
        <Route
          path="/admin"
          element={
            <PageContainer title={PAGE_TITLES.admin}>
              <AdminPage bump={bump} />
            </PageContainer>
          }
        />
        <Route
          path="/balances"
          element={
            <PageContainer title={PAGE_TITLES.balances}>
              <BalancesPage refreshToken={refreshToken} />
            </PageContainer>
          }
        />
        <Route path="*" element={<Navigate to="/ledger" replace />} />
      </Routes>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
